import { createContext, useContext, useEffect, useState } from 'react'
import { messages } from './messages'

const LanguageContext = createContext(null)
const LANGUAGE_KEY = 'canon-autosync.language'

function initialLanguage() {
  try {
    const saved = localStorage.getItem(LANGUAGE_KEY)
    if (saved === 'en' || saved === 'zh-CN') return saved
  } catch {
    // Some embedded webviews disable storage.
  }
  return /^zh(?:-|$)/i.test(navigator.language) ? 'zh-CN' : 'en'
}

export function LanguageProvider({ children }) {
  const [language, setLanguage] = useState(initialLanguage)

  useEffect(() => {
    document.documentElement.lang = language
    try {
      localStorage.setItem(LANGUAGE_KEY, language)
    } catch {
      // The current session can still switch languages.
    }
  }, [language])

  const t = (key, values) => {
    const text = messages[key][language === 'en' ? 1 : 0]
    return values ? text.replace(/\{(\w+)\}/g, (_, name) => values[name] ?? '') : text
  }

  return (
    <LanguageContext.Provider value={{ language, setLanguage, t }}>
      {children}
    </LanguageContext.Provider>
  )
}

export function useLanguage() {
  return useContext(LanguageContext)
}
