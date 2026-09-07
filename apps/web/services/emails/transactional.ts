import 'server-only'
import { send } from './resend'
import { EMAIL_SIGNOFF } from '@components/Emails/LearnHouseEmail'
import { getConfig, getMainDomainUri } from '@services/config/config'

// Non-billing transactional emails (welcome, contact). Same never-throw contract
// as the billing mails: fire-and-forget, no-op without RESEND_API_KEY.
//
// HI-HA: subject, heading and CTA used to name the upstream product and link to
// learnhouse.io — a third party's site — in mail signed by this instance. They
// now read from EMAIL_SIGNOFF, the same constant the shared template signs with,
// and the CTA points at this deployment's own domain. Inert on campus.hi-ha.be
// (send() returns early outside SaaS mode), and both functions below are
// currently unreferenced; aligned anyway so nothing can ship the wrong brand if
// a caller reappears.

// Mailbox that contact-form messages are relayed to. Upstream defaulted to a
// learnhouse.app address, which would have forwarded a visitor's message to a
// third party; default to this instance's own domain instead. Resolved per call
// rather than at import so it reflects the runtime config, not the build.
function contactInbox(): string {
  const configured = getConfig('NEXT_PUBLIC_LEARNHOUSE_CONTACT_EMAIL')
  if (configured) return configured
  const host = getMainDomainUri('').replace(/^https?:\/\//, '').split(':')[0]
  return `hello@${host}`
}

export async function sendWelcomeAccountMail(args: { email: string; username?: string }): Promise<void> {
  const { email, username } = args
  await send(email, `Welcome to ${EMAIL_SIGNOFF} 👋`, {
    accentColor: '#171717',
    heading: `Welcome to ${EMAIL_SIGNOFF}!`,
    subtitle: username
      ? `Hey ${username}, we're thrilled to have you on board.`
      : "We're thrilled to have you on board.",
    body: "You're ready to build and share courses. Here's how to get the most out of it:",
    bulletPoints: [
      'Create your first course and add content in minutes.',
      'Invite learners and track their progress.',
      'Brand your school and share it with the world.',
    ],
    cta: { label: 'Get started', href: getMainDomainUri('/home') },
  })
}

export async function sendContactMail(args: {
  fromEmail: string
  name?: string
  message: string
  to?: string
}): Promise<void> {
  const { fromEmail, name, message, to } = args
  await send(to || contactInbox(), `New contact form message from ${name || fromEmail}`, {
    accentColor: '#171717',
    heading: 'New contact message',
    subtitle: `From ${name ? `${name} · ` : ''}${fromEmail}`,
    body: message,
  })
}

// NOTE: org-created / org-deleted / account-deleted confirmation emails are
// deliberately NOT sent from here. Unlike Stripe/billing mails (which the web
// webhook owns), user/org lifecycle is owned by apps/api, which has its own
// email service — those confirmations belong there to avoid duplicate sends.
