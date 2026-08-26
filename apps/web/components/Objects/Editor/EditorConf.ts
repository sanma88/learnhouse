import { Link as LinkExtension } from '@tiptap/extension-link'
import { Plugin, PluginKey } from '@tiptap/pm/state'

// Link extension aware of same-page anchors (href="#heading-…"): they are
// rendered without target="_blank"/rel and a click scrolls in place instead of
// opening a new tab. Every other link keeps the stock behavior.
const AnchorAwareLink = LinkExtension.extend({
  renderHTML(props) {
    // The parent implementation validates the href through isAllowedUri and
    // neutralizes disallowed ones (href="") — keep that as-is and only
    // post-process the merged attributes.
    const rendered: any = this.parent?.(props)
    const attrs = rendered?.[1]
    if (attrs && typeof attrs.href === 'string' && attrs.href.startsWith('#')) {
      // In-page anchor: a new tab makes no sense
      delete attrs.target
      delete attrs.rel
    }
    return rendered
  },
  addProseMirrorPlugins() {
    // Placed BEFORE the stock plugins: ProseMirror's someProp() stops at the
    // first handleClick returning true, so anchor clicks never reach the
    // stock openOnClick handler (which would window.open them in a new tab).
    const handleAnchorClick = new Plugin({
      key: new PluginKey('handleAnchorClick'),
      props: {
        handleClick: (_view, _pos, event) => {
          if (event.button !== 0) {
            return false
          }
          const target = event.target as HTMLElement | null
          const href = target?.closest?.('a')?.getAttribute('href')
          if (!href || !href.startsWith('#')) {
            // Normal link: fall through to the stock clickHandler
            return false
          }
          try {
            document
              .getElementById(decodeURIComponent(href.slice(1)))
              ?.scrollIntoView()
          } catch {
            // Malformed percent-encoding in the fragment — ignore
          }
          return true
        },
      },
    })
    return [handleAnchorClick, ...(this.parent?.() || [])]
  },
})

export const getLinkExtension = () => {
  return AnchorAwareLink.configure({
    openOnClick: true,
    HTMLAttributes: {
      target: '_blank',
      rel: 'noopener noreferrer',
    },
    autolink: true,
    defaultProtocol: 'https',
    protocols: ['http', 'https'],
    isAllowedUri: (url: string, ctx: any) => {
      try {
        // same-page anchors (e.g. "#heading-introduction") are allowed as-is
        if (url.startsWith('#')) {
          return true
        }

        // construct URL
        const parsedUrl = url.includes(':') ? new URL(url) : new URL(`${ctx.defaultProtocol}://${url}`)

        // use default validation
        if (!ctx.defaultValidate(parsedUrl.href)) {
          return false
        }

        // disallowed protocols
        const disallowedProtocols = ['ftp', 'file', 'mailto']
        const protocol = parsedUrl.protocol.replace(':', '')

        if (disallowedProtocols.includes(protocol)) {
          return false
        }

        // only allow protocols specified in ctx.protocols
        const allowedProtocols = ctx.protocols.map((p: any) => (typeof p === 'string' ? p : p.scheme))

        if (!allowedProtocols.includes(protocol)) {
          return false
        }

        // all checks have passed
        return true
      } catch {
        return false
      }
    },
    shouldAutoLink: (url: string) => {
      try {
        // construct URL
        const parsedUrl = url.includes(':') ? new URL(url) : new URL(`https://${url}`)

        // only auto-link if the domain is not in the disallowed list
        const disallowedDomains = ['example-no-autolink.com', 'another-no-autolink.com']
        const domain = parsedUrl.hostname

        return !disallowedDomains.includes(domain)
      } catch {
        return false
      }
    },
  })
} 