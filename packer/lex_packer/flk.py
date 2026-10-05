"""flk 法规每日增量 → 可分发的增量 bundle（spec lex-private-distribution「增量更新通道」）。

增量源实址（2026-10-05 探明并实测可达）：
- 产源：``fd-law-data`` 仓 ``bin/flk-crawl.mjs``，K8s CronJob ``flk-law-crawl``
  （``k8s/flk-crawl-cronjob.yaml``，schedule ``30 2 * * *`` = 02:30 Asia/Shanghai，
  namespace ``scraw``，镜像 ``fd-law-data:v0.1.9``）。
- 产物落点：RustFS ``http://100.64.0.11:9000`` bucket ``laws`` 的 ``incremental/`` 前缀：
    ``incremental/YYYY/MM/flk-YYYY-MM-DD[-partNNN].jsonl``   当日新增/变更法规（记录列= laws 表）
    ``incremental/YYYY/MM/flk-YYYY-MM-DD.manifest.json``     当日 flk manifest（counts/byCode/window/finishedAt）
    ``incremental/state.json`` / ``incremental/backfill-state.json``   水位与已见集合（crawler 私有状态）
- 无新增的日期只有 ``.manifest.json``（``emitted: 0``），没有 jsonl——本模块如实记录为
  ``empty_days``，不伪造文件。
- 客户侧应用语义：按 ``(src_db, source_id)`` 幂等 upsert，再按条级口径重切
  （``fd-law-data/bin/flk-load.mjs`` 的 idempotent INSERT 口径）；校验不过不应用。

本模块只做「包一层 manifest」：日增量文件**原样**收入 bundle（字节与源对象一致，
sha256 即源对象摘要），逐组件 provenance + 行数；``--tar`` 产出 pack.tar + sidecar，
即可由 ``fd_lex_api`` 分发端点（/api/distribution）按同一契约服务。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .errors import BuildError
from .guard import assert_components_packable
from .manifest import (
    Component,
    Manifest,
    Provenance,
    build_pack_tar,
    read_digest_sidecar,
    validate_host_deltas,
    write_manifest,
)
from .registry import load_registry
from .version import bundle_id as make_bundle_id

BUILDER = "lex-packer/0.1.0"
FLK_CLASS = "laws_daily"
FLK_SOURCE_NOTE = (
    "RustFS bucket laws，incremental/ 前缀；产源 fd-law-data flk-law-crawl "
    "CronJob（30 2 * * * Asia/Shanghai）"
)
APPLY_SEMANTICS = (
    "按 (src_db, source_id) 幂等 upsert，随后按条级口径重切；"
    "pack.tar/sha256 校验不过不应用（坏包不应用），客户数据保持原状"
)

FLK_KEY_RE = re.compile(
    r"^(?:(?P<prefix>(?:[^/]+/)*))?(?P<y>\d{4})/(?P<m>\d{2})/"
    r"flk-(?P<date>\d{4}-\d{2}-\d{2})(?P<part>-part\d+)?(?P<kind>\.jsonl|\.manifest\.json)$"
)
_FLAT_RE = re.compile(
    r"^flk-(?P<date>\d{4}-\d{2}-\d{2})(?P<part>-part\d+)?(?P<kind>\.jsonl|\.manifest\.json)$"
)


@dataclass(frozen=True)
class FlkObject:
    """RustFS/S3（或本地镜像目录）里的一个 flk 日增量对象。"""

    key: str  # 源 key（S3）或相对路径（本地）
    date: str
    part: str | None
    kind: str  # "jsonl" | "manifest"
    channel: str = "incremental"  # 日更 incremental / 历史回填 backfill
    size: int | None = None

    @property
    def stem(self) -> str:
        suffix = self.part or ""
        prefix = "" if self.channel == "incremental" else f"{self.channel}-"
        return f"flk-{prefix}{self.date}{suffix}"

    @property
    def is_data(self) -> bool:
        return self.kind == "jsonl"

    def pack_path(self) -> str:
        """包内相对路径（保留 源 channel/y/m 层级，日更与回填不互相覆盖）。"""
        y, m = self.date[:4], self.date[5:7]
        name = f"{self.stem}.jsonl" if self.is_data else f"{self.stem}.manifest.json"
        return f"data/flk/{self.channel}/{y}/{m}/{name}"


def parse_flk_key(key: str) -> FlkObject | None:
    """解析 flk 对象 key（S3 key 或本地相对路径）；非 flk 对象返回 None。

    支持 ``incremental/`` 日更与 ``incremental/backfill/`` 历史回填两个通道
    （回填组件名带 ``backfill-`` 前缀，避免与同日日更文件混淆）。
    """
    normalized = key.strip().lstrip("./")
    match = FLK_KEY_RE.match(normalized)
    if not match and "/" not in normalized:
        match = _FLAT_RE.match(normalized)
    if not match:
        return None
    prefix = (match.groupdict().get("prefix") or "").strip("/")
    channel = prefix.split("/")[-1] if prefix else "incremental"
    kind = "jsonl" if match.group("kind") == ".jsonl" else "manifest"
    return FlkObject(
        key=normalized,
        date=match.group("date"),
        part=match.group("part"),
        kind=kind,
        channel=channel,
    )


def discover_local(local_dir: Path | str) -> list[FlkObject]:
    """本地镜像目录发现（布局同 RustFS ``incremental/``；也接受扁平目录）。"""
    root = Path(local_dir)
    found: dict[str, FlkObject] = {}
    for path in sorted(root.rglob("flk-*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        parsed = parse_flk_key(rel)
        if parsed is None:
            continue
        found[parsed.key] = FlkObject(
            key=rel, date=parsed.date, part=parsed.part, kind=parsed.kind,
            channel=parsed.channel, size=path.stat().st_size,
        )
    return sorted(found.values(), key=lambda item: (item.date, item.channel, item.kind, item.key))


def group_by_date(objects: Iterable[FlkObject]) -> dict[str, list[FlkObject]]:
    grouped: dict[str, list[FlkObject]] = {}
    for item in objects:
        grouped.setdefault(item.date, []).append(item)
    return {
        date: sorted(items, key=lambda i: (i.channel, i.kind, i.key))
        for date, items in sorted(grouped.items())
    }


def select_dates(
    grouped: dict[str, list[FlkObject]],
    *,
    days: int | None = None,
    date: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[str]:
    """选择要收录的日期。

    - ``date``：精确到某日；该日只有 manifest（emitted=0）时返回该日，由调用侧按
      「空增量不产出 bundle」拒绝。
    - ``days`` / ``since`` / ``until``：在**有数据（jsonl）的日期**上筛选（``days`` = 最新 N 个），
      然后把这些日期最早日之后、范围内的空增量日一并收进来如实记录（尾部空增量日
      不阻塞出货——客户端由 bundle 里的 empty_days 得知"该日无新增"）。
    """
    dates = sorted(grouped)
    if date:
        if date not in grouped:
            raise BuildError(f"flk 增量源没有 {date} 的对象（可选日期：{dates}）")
        return [date]
    data_dates = [d for d in dates if any(item.is_data for item in grouped[d])]
    if since:
        data_dates = [d for d in data_dates if d >= since]
    if until:
        data_dates = [d for d in data_dates if d <= until]
    if days is not None:
        if days < 1:
            raise BuildError(f"--days 必须 >= 1，得到 {days}")
        data_dates = data_dates[-days:]
    if not data_dates:
        raise BuildError(
            "flk 增量源在选定范围内没有任何日增量 JSONL（可能只有 emitted=0 的空增量日 manifest）；"
            "请扩大范围或换日期"
        )
    lower = min(data_dates)
    upper = until or max(dates)
    return [d for d in dates if lower <= d <= upper]


class RustFsFlkSource:
    """RustFS/S3 只读拉取（boto3，可选依赖：pip install 'lex-packer[s3]'）。"""

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str = "laws",
        prefix: str = "incremental/",
        region: str = "us-east-1",
        timeout: int = 30,
    ) -> None:
        try:
            import boto3  # type: ignore[import-not-found]
            from botocore.config import Config  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - 环境相关
            raise BuildError(
                "RustFS 拉取需要 boto3：pip install 'lex-packer[s3]'（或 --local-dir 走离线镜像）"
            ) from exc
        self.bucket = bucket
        self.prefix = prefix
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(
                connect_timeout=timeout,
                read_timeout=timeout,
                retries={"max_attempts": 2},
                s3={"addressing_style": "path"},
            ),
        )

    def list_objects(self) -> list[FlkObject]:
        found: dict[str, FlkObject] = {}
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for item in page.get("Contents", []):
                parsed = parse_flk_key(item["Key"])
                if parsed is None:
                    continue
                found[parsed.key] = FlkObject(
                    key=parsed.key, date=parsed.date, part=parsed.part,
                    kind=parsed.kind, channel=parsed.channel, size=int(item["Size"]),
                )
        return sorted(found.values(), key=lambda item: (item.date, item.channel, item.kind, item.key))

    def fetch(self, obj: FlkObject, dest: Path) -> tuple[int, str, int | None]:
        """下载到 dest，同时算 sha256 与行数（一趟）；返回 (bytes, sha256, rows)。"""
        dest.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        rows: int | None = 0 if obj.is_data else None
        body = self._client.get_object(Bucket=self.bucket, Key=obj.key)["Body"]
        with open(dest, "wb") as handle:
            for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                handle.write(chunk)
                digest.update(chunk)
                size += len(chunk)
                if rows is not None:
                    rows += chunk.count(b"\n")
        # JSONL 末行可能无换行：按收尾字节补齐（仅数据文件）
        if rows == 0 and size > 0 and obj.is_data:
            rows = 1
        return size, digest.hexdigest(), rows

    def read_json(self, key: str) -> dict[str, Any]:
        return json.loads(self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read())


def _measure_local(path: Path, *, count_rows: bool) -> tuple[int, str, int | None]:
    digest = hashlib.sha256()
    size = 0
    rows: int | None = 0 if count_rows else None
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            if rows is not None:
                rows += chunk.count(b"\n")
    if count_rows and size > 0 and rows == 0:
        rows = 1
    return size, digest.hexdigest(), rows


def _copy_local(source: Path, dest: Path, *, count_rows: bool) -> tuple[int, str, int | None]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    size, sha256, rows = _measure_local(source, count_rows=count_rows)
    shutil.copyfile(source, dest)
    return size, sha256, rows


@dataclass
class FlkIncrementalResult:
    bundle_id: str
    pack_dir: Path
    pack_file: Path
    pack_sha256: str | None
    manifest: Manifest
    dates: list[str]
    empty_days: list[str]
    components: list[Component] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "ok": True,
            "bundle_id": self.bundle_id,
            "pack_dir": str(self.pack_dir),
            "pack_file": str(self.pack_file),
            "pack_sha256": self.pack_sha256,
            "dates": self.dates,
            "empty_days": self.empty_days,
            "rows_total": sum(c.rows or 0 for c in self.manifest.components),
            "bytes_total": sum(c.bytes for c in self.manifest.components),
            "components": [
                {"name": c.name, "path": c.path, "bytes": c.bytes, "sha256": c.sha256,
                 "rows": c.rows, "provenance": c.provenance.to_dict() if c.provenance else None}
                for c in self.manifest.components
            ],
        }


def build_flk_incremental(
    objects: Iterable[FlkObject],
    out_dir: Path,
    version: str,
    *,
    bucket: str = "laws",
    source_note: str = FLK_SOURCE_NOTE,
    local_root: Path | None = None,
    s3_source: RustFsFlkSource | None = None,
    flk_manifests: dict[str, dict[str, Any]] | None = None,
    days: int | None = None,
    date: str | None = None,
    since: str | None = None,
    until: str | None = None,
    built_at: str | None = None,
    mtime: int | None = None,
    make_tar: bool = True,
    registry_path: Path | None = None,
) -> FlkIncrementalResult:
    """把选定的 flk 日增量对象打成可分发的增量 bundle。"""
    objects = list(objects)
    if not objects:
        raise BuildError("flk 增量源没有任何对象（检查 bucket/prefix 或 --local-dir）")
    grouped = group_by_date(objects)
    dates = select_dates(grouped, days=days, date=date, since=since, until=until)
    selected = [item for d in dates for item in grouped[d]]
    if not any(item.is_data for item in selected):
        raise BuildError(
            f"选定日期 {dates} 只有 flk manifest、没有日增量 JSONL（emitted=0 的空增量日）；"
            "请扩大范围或换日期——空增量不产出 bundle"
        )

    as_of = max(dates)
    bundle_name = make_bundle_id(version, "citation", form="incremental", as_of=as_of)
    built_at = built_at or datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    pack_dir = Path(out_dir) / bundle_name
    pack_dir.mkdir(parents=True, exist_ok=True)

    registry = load_registry(str(registry_path) if registry_path else None)
    guard = assert_components_packable(registry, [FLK_CLASS])

    manifests: dict[str, dict[str, Any]] = dict(flk_manifests or {})
    components: list[Component] = []
    for obj in selected:
        target = pack_dir / obj.pack_path()
        if s3_source is not None:
            size, sha256, rows = s3_source.fetch(obj, target)
            origin = f"rustfs://{s3_source.bucket}/{obj.key}"
        else:
            assert local_root is not None
            source = Path(local_root) / obj.key
            if not source.is_file():
                raise BuildError(f"本地 flk 镜像缺对象：{source}")
            size, sha256, rows = _copy_local(source, target, count_rows=obj.is_data)
            origin = f"file://{source}"
        if obj.is_data:
            date_manifest = manifests.get(obj.key)
            manifest_bits = (
                f"；flk manifest counts={date_manifest.get('counts')}"
                if date_manifest
                else ""
            )
            components.append(
                Component(
                    name=obj.stem,
                    kind="data",
                    tier="citation",
                    path=obj.pack_path(),
                    bytes=size,
                    sha256=sha256,
                    rows=rows,
                    provenance=Provenance(
                        source=origin,
                        stable_id="(src_db, source_id)",
                        as_of=obj.date,
                        snapshot=f"flk {obj.channel} 增量法规（laws 表列）{manifest_bits}",
                    ),
                    note=f"class={FLK_CLASS};channel={obj.channel}",
                )
            )
        else:
            # flk 当日 manifest 随包（完整性 + counts/window 证据；不伪造数据）；
            # 同时全文保留在 data_tiers.incremental.flk_manifests（证据内联）
            components.append(
                Component(
                    name=obj.stem + ".manifest",
                    kind="metadata",
                    tier="citation",
                    path=obj.pack_path(),
                    bytes=size,
                    sha256=sha256,
                    rows=None,
                    provenance=Provenance(
                        source=origin,
                        stable_id="date",
                        as_of=obj.date,
                        snapshot="flk 当日 manifest（counts/byCode/window/finishedAt）",
                    ),
                    note=f"class={FLK_CLASS};channel={obj.channel}",
                )
            )

    empty_days = sorted(
        d for d in dates if not any(item.is_data for item in grouped[d])
    )
    data_dates = sorted({item.date for item in selected if item.is_data})
    # 证据内联只带选中对象的 manifest（不掺入未选日期）
    selected_keys = {item.key for item in selected}
    selected_manifests = {k: manifests[k] for k in sorted(manifests) if k in selected_keys}

    manifest = Manifest(
        bundle={
            "id": bundle_name,
            "name": "lex-dist",
            "version": version,
            "form": "incremental",
            "tier": "citation",
            "as_of": as_of,
            "built_at": built_at,
            "builder": BUILDER,
            "hosts": ["cloud", "on_prem"],
            "base_bundle": None,
            "incremental_kind": "flk-daily",
            "content_digest": "",
        },
        components=components,
    )
    manifest.data_tiers = {
        "tier": "citation",
        "label": "引用包（法规库+法规目录+指导案例，必选底座）",
        "included": [FLK_CLASS],
        "citation_pack_required": True,
        "incremental": {
            "kind": "flk-daily",
            "dates": dates,
            "data_dates": data_dates,
            "empty_days": empty_days,
            "bucket": bucket,
            "source_note": source_note,
            "apply_semantics": APPLY_SEMANTICS,
            "flk_manifests": selected_manifests,
            "note": (
                "拉模式：客户侧定时任务经 /api/distribution 拉取本 bundle（凭据鉴权 + sha256 校验）；"
                "无法联网客户走同一 bundle 的离线整包介质形态"
            ),
        },
    }
    manifest.guards = {
        "cloud_locked_guard": guard.to_dict(),
        "policy": "cloud_locked 组件不得出现在 bundle（lex-private-distribution 可私有化不变量）",
    }
    manifest.missing_inputs = []
    manifest.bundle["content_digest"] = manifest.content_digest()
    validate_host_deltas()
    write_manifest(pack_dir, manifest)

    pack_file = pack_dir / "pack.tar"
    pack_sha256: str | None = None
    if make_tar:
        pack_file = build_pack_tar(pack_dir, mtime=mtime)
        pack_sha256 = read_digest_sidecar(pack_dir)
    return FlkIncrementalResult(
        bundle_id=bundle_name,
        pack_dir=pack_dir,
        pack_file=pack_file,
        pack_sha256=pack_sha256,
        manifest=manifest,
        dates=dates,
        empty_days=empty_days,
        components=components,
    )


def iter_flk_source(
    *,
    local_dir: Path | str | None = None,
    endpoint: str | None = None,
    access_key: str | None = None,
    secret_key: str | None = None,
    bucket: str = "laws",
    prefix: str = "incremental/",
) -> tuple[list[FlkObject], RustFsFlkSource | None, dict[str, dict[str, Any]]]:
    """统一入口：返回（对象清单, S3 源或 None, 已读到的 flk 日 manifest）。"""
    if local_dir:
        objects = discover_local(local_dir)
        manifests: dict[str, dict[str, Any]] = {}
        for item in objects:
            if item.kind == "manifest":
                try:
                    manifests[item.key] = json.loads(
                        (Path(local_dir) / item.key).read_text(encoding="utf-8")
                    )
                except json.JSONDecodeError:
                    continue
        return objects, None, manifests
    if not (endpoint and access_key and secret_key):
        raise BuildError(
            "需要 --local-dir 或完整 RustFS 连接（--endpoint/--access-key/--secret-key 或 env "
            "FDLEX_RUSTFS_ENDPOINT/FDLEX_RUSTFS_ACCESS_KEY/FDLEX_RUSTFS_SECRET_KEY）"
        )
    source = RustFsFlkSource(
        endpoint=endpoint, access_key=access_key, secret_key=secret_key,
        bucket=bucket, prefix=prefix,
    )
    objects = source.list_objects()
    manifests = {}
    for item in objects:
        if item.kind == "manifest":
            try:
                manifests[item.key] = source.read_json(item.key)
            except Exception:  # noqa: BLE001 - manifest 读不到不阻塞数据文件
                continue
    return objects, source, manifests


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """抽样读取包内 flk JSONL（构建后可复现性硬证据用）。"""
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)