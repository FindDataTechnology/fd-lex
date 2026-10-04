import { BrowserRouter, Route, Routes } from 'react-router-dom'

import Admin from './pages/Admin'
import Console from './pages/Console'
import Home from './pages/Home'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/console" element={<Console />} />
        <Route path="/admin" element={<Admin />} />
      </Routes>
    </BrowserRouter>
  )
}

export default App
