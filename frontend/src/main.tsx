import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import App from './App.tsx'
import { AuthGate } from './auth.tsx'

// AuthGate wraps App rather than living inside it: App's mount effects
// all call the API, so the tree must not exist before there is a session.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthGate>
      <App />
    </AuthGate>
  </StrictMode>,
)
