'use client'
// Shared legal/footer bits, ported from the platform's look.
//
// AuthFooter   — the "By continuing, you agree to … Terms of Service and
//                Privacy Policy." line shown under the auth forms.
// CopyrightFooter — the "© {year} LearnHouse, Inc." line for app surfaces
//                (the apex /home hub, the onboarding page, …).
//
// Legal pages live on the marketing/platform site, so links resolve via
// getPlatformUrl() with a sensible public fallback.
import React from 'react'
import Link from 'next/link'
import { useTranslation } from 'react-i18next'
import { getPlatformUrl, getConfig } from '@services/config/config'

// HI-HA: the upstream defaults sent every visitor to learnhouse.io's own Terms
// and Privacy pages. On a self-hosted instance that is a false statement — our
// users are not contracting with LearnHouse, Inc. — so the legal links now
// render ONLY when this deployment actually publishes such pages, i.e. when a
// platform URL is configured. Otherwise the nav is simply omitted.
const TERMS_URL = getPlatformUrl('/terms')
const PRIVACY_URL = getPlatformUrl('/privacy')
const HAS_LEGAL_PAGES = Boolean(TERMS_URL && PRIVACY_URL)

// HI-HA: AGPL-3.0 art. 13 — anyone interacting with this program over a network
// must be offered the Corresponding Source of the running version. The NOTICE
// file alone does not reach them, so the offer is surfaced in the UI itself.
// Read through getConfig (the runtime mechanism) rather than process.env, so a
// prebuilt image can be repointed from the environment: NEXT_PUBLIC_ values are
// inlined at build time and would otherwise freeze this URL into the image.
// Override with NEXT_PUBLIC_LEARNHOUSE_SOURCE_URL when running your own fork.
const SOURCE_URL =
  getConfig('NEXT_PUBLIC_LEARNHOUSE_SOURCE_URL') || 'https://github.com/sanma88/learnhouse'

export function AuthFooter({ className = '' }: { className?: string }) {
  const { t } = useTranslation()
  return (
    <div className={`pb-8 pt-6 text-center px-6 ${className}`}>
      {HAS_LEGAL_PAGES && (
        <p className="text-[13px] text-black/30 font-medium">
          {t('auth.terms_text')}{' '}
          <Link
            href={TERMS_URL as string}
            target="_blank"
            rel="noopener noreferrer"
            className="text-black/50 hover:text-black/70 transition-colors"
          >
            {t('auth.terms_of_service', { defaultValue: 'Terms of Service' })}
          </Link>{' '}
          {t('auth.and', { defaultValue: 'and' })}{' '}
          <Link
            href={PRIVACY_URL as string}
            target="_blank"
            rel="noopener noreferrer"
            className="text-black/50 hover:text-black/70 transition-colors"
          >
            {t('auth.privacy_policy', { defaultValue: 'Privacy Policy' })}
          </Link>
          .
        </p>
      )}
      <p className="text-[13px] text-black/30 font-medium pt-1">
        <Link
          href={SOURCE_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="text-black/50 hover:text-black/70 transition-colors"
        >
          {t('common.source_code', { defaultValue: 'Source code' })}
        </Link>
      </p>
    </div>
  )
}

export function CopyrightFooter({
  year,
  className = '',
  tone = 'light',
}: {
  year: number
  className?: string
  // `light` → dark text on light bg; `dark` → light text on dark bg.
  tone?: 'light' | 'dark'
}) {
  const { t } = useTranslation()
  const base = tone === 'dark' ? 'text-white/40' : 'text-black/35'
  const link = tone === 'dark' ? 'text-white/60 hover:text-white/80' : 'text-black/55 hover:text-black/75'
  return (
    <footer className={`w-full py-6 px-6 ${className}`}>
      <div className="flex flex-col sm:flex-row items-center justify-center gap-x-5 gap-y-2 text-[13px] font-medium">
        <p className={base}>{t('common.copyright', { year })}</p>
        <nav className="flex items-center gap-x-5">
          {HAS_LEGAL_PAGES && (
            <>
              <Link
                href={TERMS_URL as string}
                target="_blank"
                rel="noopener noreferrer"
                className={`${link} transition-colors`}
              >
                {t('auth.terms_of_service', { defaultValue: 'Terms of Service' })}
              </Link>
              <Link
                href={PRIVACY_URL as string}
                target="_blank"
                rel="noopener noreferrer"
                className={`${link} transition-colors`}
              >
                {t('auth.privacy_policy', { defaultValue: 'Privacy Policy' })}
              </Link>
            </>
          )}
          <Link
            href={SOURCE_URL}
            target="_blank"
            rel="noopener noreferrer"
            className={`${link} transition-colors`}
          >
            {t('common.source_code', { defaultValue: 'Source code' })}
          </Link>
        </nav>
      </div>
    </footer>
  )
}
