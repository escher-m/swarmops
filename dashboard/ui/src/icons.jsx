/* Icons, drawn here rather than pulled from an icon package.
 *
 * lucide-react is 45 MB on disk and Vite's library mode was not tree-shaking
 * it, so eight glyphs were costing most of a megabyte. These are the same paths
 * the Jinja sidebar already uses, which also means the palette and the
 * navigation cannot drift apart visually.
 */

const base = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
};

const Icon = ({ size = 16, children, ...rest }) => (
  <svg viewBox="0 0 16 16" width={size} height={size} {...base} {...rest}>
    {children}
  </svg>
);

export const Search = (p) => (
  <Icon {...p}><circle cx="7.2" cy="7.2" r="4.6" /><path d="M10.6 10.6L14 14" /></Icon>
);
export const Table = (p) => (
  <Icon {...p}><rect x="2" y="2.5" width="12" height="11" rx="2" /><path d="M5 6h6M5 9h6M5 11.5h3.5" /></Icon>
);
export const TriangleAlert = (p) => (
  <Icon {...p}><path d="M8 2l6 11H2L8 2z" /><path d="M8 6.5v3M8 11.2v.1" /></Icon>
);
export const Check = (p) => (
  <Icon {...p}><path d="M3 8.5l3.2 3.2L13 5" /></Icon>
);
export const ShieldCheck = (p) => (
  <Icon {...p}>
    <path d="M8 1.8l5 2v4c0 3-2.1 5.5-5 6.4-2.9-.9-5-3.4-5-6.4v-4l5-2z" />
    <path d="M5.8 8l1.6 1.6 3-3.2" />
  </Icon>
);
export const Download = (p) => (
  <Icon {...p}><path d="M8 2v7.5M5.2 7l2.8 2.8L10.8 7M3 12.5h10" /></Icon>
);
export const FileText = (p) => (
  <Icon {...p}><path d="M9 1.8H4.5A1.5 1.5 0 003 3.3v9.4a1.5 1.5 0 001.5 1.5h7a1.5 1.5 0 001.5-1.5V5.8z" /><path d="M9 1.8v4h4M5.6 8.6h4.8M5.6 11h3.2" /></Icon>
);
