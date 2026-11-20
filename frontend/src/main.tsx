import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import './styles/tokens.css'
import './styles/app.css'

const el = document.getElementById('root')
if (!el) throw new Error('#root not found')

// React 18 挂载时会把 index.html 里的首屏骨架整体替换掉，
// 所以用户看到的是"骨架 → 内容"，中间没有白屏。
createRoot(el).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
