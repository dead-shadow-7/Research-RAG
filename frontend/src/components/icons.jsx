/**
 * Monotone icon set.
 *
 * Hand-drawn inline SVG rather than an icon package: it keeps the bundle unchanged,
 * and more importantly it keeps one consistent stroke weight and corner treatment
 * across every glyph, which is most of what makes an icon set look deliberate.
 *
 * All icons inherit `currentColor` and size from the `size` prop, so colour is set by
 * the surrounding text style and never hardcoded here.
 */

function Svg({ size = 16, children, ...props }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {children}
    </svg>
  )
}

/* --- document types: distinguished by silhouette, so they read at 16px --- */

/* PDF: a notched page carrying a solid badge. The cut corner plus a filled mass is
   readable at 16px, where inner line detail is not. */
export const IconPdf = (p) => (
  <Svg {...p}>
    <path d="M13 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V8z" />
    <path d="M13 3v5h5" />
    <rect x="8.5" y="12.75" width="7" height="4.75" rx="1" fill="currentColor" stroke="none" />
  </Svg>
)

/* Word: a plain rounded sheet of text -- no notch, so its outline differs from the
   PDF at a glance, and it keeps a container so it is not confused with plain text. */
export const IconDoc = (p) => (
  <Svg {...p}>
    <rect x="5.5" y="3.5" width="13" height="17" rx="2" />
    <path d="M8.75 8.25h6.5M8.75 12h6.5M8.75 15.75h3.5" />
  </Svg>
)

export const IconSheet = (p) => (
  <Svg {...p}>
    <rect x="3.5" y="4.5" width="17" height="15" rx="1.5" />
    <path d="M3.5 9.5h17M9.5 9.5v10M3.5 14.5h17" />
  </Svg>
)

export const IconText = (p) => (
  <Svg {...p}>
    <path d="M5 6.5h14M5 11h14M5 15.5h10M5 20h6" />
  </Svg>
)

export const IconLink = (p) => (
  <Svg {...p}>
    <path d="M10.5 13.5a4 4 0 0 0 5.7 0l2.8-2.8a4 4 0 1 0-5.7-5.7l-1.6 1.6" />
    <path d="M13.5 10.5a4 4 0 0 0-5.7 0l-2.8 2.8a4 4 0 1 0 5.7 5.7l1.6-1.6" />
  </Svg>
)

const BY_SOURCE = {
  pdf: IconPdf,
  docx: IconDoc,
  xlsx: IconSheet,
  txt: IconText,
  url: IconLink,
}

export function SourceIcon({ type, ...props }) {
  const Glyph = BY_SOURCE[type] ?? IconDoc
  return <Glyph {...props} />
}

/* --- actions and status --- */

export const IconUpload = (p) => (
  <Svg {...p}>
    <path d="M12 15.5V4m0 0L8 8m4-4 4 4" />
    <path d="M4.5 15v3.5a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5V15" />
  </Svg>
)

export const IconTrash = (p) => (
  <Svg {...p}>
    <path d="M4.5 6.5h15M9.5 6.5V5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v1.5" />
    <path d="M6.5 6.5 7.4 19a1 1 0 0 0 1 .9h7.2a1 1 0 0 0 1-.9l.9-12.5" />
    <path d="M10.5 10v6M13.5 10v6" />
  </Svg>
)

export const IconSend = (p) => (
  <Svg {...p}>
    <path d="M12 19.5V5m0 0-6 6m6-6 6 6" />
  </Svg>
)

export const IconClose = (p) => (
  <Svg {...p}>
    <path d="M6 6l12 12M18 6 6 18" />
  </Svg>
)

export const IconPlus = (p) => (
  <Svg {...p}>
    <path d="M12 5v14M5 12h14" />
  </Svg>
)

export const IconCheck = (p) => (
  <Svg {...p}>
    <path d="m5 12.5 4.5 4.5L19 7" />
  </Svg>
)

export const IconAlert = (p) => (
  <Svg {...p}>
    <path d="M12 4.5 2.8 20h18.4z" />
    <path d="M12 10v4M12 17h.01" />
  </Svg>
)

/** Indexing: an open arc that reads as motion once it spins. */
export const IconSpinner = ({ className = '', ...p }) => (
  <Svg className={`animate-spin ${className}`} {...p}>
    <path d="M12 3a9 9 0 1 0 9 9" />
  </Svg>
)

export const IconQuote = (p) => (
  <Svg {...p}>
    <path d="M9.5 6.5C6.9 8 5.5 10.2 5.5 13v4.5h5V12H8c0-1.8.6-3.1 1.9-4zM19.5 6.5C16.9 8 15.5 10.2 15.5 13v4.5h5V12H18c0-1.8.6-3.1 1.9-4z" />
  </Svg>
)
