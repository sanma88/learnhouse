/**
 * Normalises ProseMirror JSON on its way *into* TipTap.
 *
 * Both fixes have to happen before the editor is constructed: TipTap builds its
 * document from the `content` option, and NoTextInput filters out every later
 * transaction (`setContent` returns true but changes nothing), so a document
 * that arrives broken can never be repaired afterwards.
 *
 * 1. Mark names — TipTap uses 'bold'/'italic', AI generation sometimes emits
 *    'strong'/'em'.
 * 2. Empty text nodes — ProseMirror throws `Empty text nodes are not allowed`
 *    on `{"type":"text","text":""}`. TipTap catches that and falls back to an
 *    *empty document*, logging only a console warning: a single bad node blanks
 *    the entire activity with no visible error. Dropping those nodes keeps the
 *    rest of the document renderable.
 */

function isEmptyTextNode(node: any): boolean {
  return (
    !!node &&
    typeof node === 'object' &&
    node.type === 'text' &&
    (typeof node.text !== 'string' || node.text === '')
  )
}

export function sanitizeTiptapContent(content: any): any {
  if (!content || typeof content !== 'object') {
    return content
  }

  if (Array.isArray(content)) {
    return content.filter((node) => !isEmptyTextNode(node)).map(sanitizeTiptapContent)
  }

  const normalized: any = { ...content }

  if (Array.isArray(normalized.marks)) {
    normalized.marks = normalized.marks.map((mark: any) => {
      if (mark.type === 'strong') {
        return { ...mark, type: 'bold' }
      }
      if (mark.type === 'em') {
        return { ...mark, type: 'italic' }
      }
      return mark
    })
  }

  if (Array.isArray(normalized.content)) {
    normalized.content = sanitizeTiptapContent(normalized.content)
  }

  return normalized
}
