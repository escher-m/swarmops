/* React islands for the server-rendered dashboard.
 *
 * This is not an SPA. Jinja owns routing, authentication and the page shell;
 * this bundle mounts interactive components into specific elements and does
 * nothing else. A page that declares no mount point pays only the cost of the
 * script tag, and pages that need no island do not load it at all.
 */

import { createRoot } from 'react-dom/client';
import { CommandSearch } from './components/watermelon/command-search.tsx';
import { FileText, TriangleAlert, ShieldCheck, Check, Table, Download } from './icons.jsx';
import './index.css';

/* Static destinations. Deliberately listed here rather than fetched: they never
   change, and a palette that cannot navigate until a request resolves feels
   broken on the first keystroke. */
function staticItems() {
  const go = (href) => () => { window.location.href = href; };
  return [
    { id: 'nav-records',   title: 'All records',        section: 'Go to', icon: <Table size={16} />,         shortcut: 'G', action: go('/') },
    { id: 'nav-incidents', title: 'Incidents',          section: 'Go to', icon: <TriangleAlert size={16} />, shortcut: 'I', action: go('/incidents') },
    { id: 'nav-approvals', title: 'Pending approvals',  section: 'Go to', icon: <Check size={16} />,         shortcut: 'A', action: go('/approvals') },
    { id: 'nav-verify',    title: 'Chain verification', section: 'Go to', icon: <ShieldCheck size={16} />,   shortcut: 'V', action: go('/verify') },
    { id: 'act-pdf',       title: 'Export audit report (PDF)', section: 'Export', icon: <Download size={16} />, action: go('/export.pdf') },
    { id: 'act-csv',       title: 'Export raw records (CSV)',  section: 'Export', icon: <Download size={16} />, action: go('/export.csv') },
  ];
}

function mountCommandSearch(el) {
  const root = createRoot(el);
  let ledgerItems = [];

  const render = () => root.render(<CommandSearch items={[...staticItems(), ...ledgerItems]} />);
  render();

  /* Ledger contents arrive after first paint. The palette is usable for
     navigation immediately and gains incidents and records once they load; a
     failed fetch simply leaves the navigation items, which is a reasonable
     degraded state for a search box. */
  fetch('/api/search', { headers: { accept: 'application/json' } })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.status))))
    .then((data) => {
      const go = (href) => () => { window.location.href = href; };
      ledgerItems = [
        ...data.incidents.map((i) => ({
          id: `inc-${i.incident_id}`,
          title: `${i.incident_id} · ${i.resource_id}`,
          section: 'Incidents',
          icon: <TriangleAlert size={16} />,
          action: go(`/incident/${encodeURIComponent(i.incident_id)}`),
        })),
        ...data.records.map((r) => ({
          id: `rec-${r.record_id}`,
          title: `${r.action_type} · ${r.summary}`,
          section: 'Recent records',
          icon: <FileText size={16} />,
          action: go(`/record/${r.record_id}`),
        })),
      ];
      render();
    })
    .catch(() => { /* navigation-only palette is a fine fallback */ });
}

const ISLANDS = {
  'command-search': mountCommandSearch,
};

document.querySelectorAll('[data-island]').forEach((el) => {
  const mount = ISLANDS[el.dataset.island];
  if (mount) mount(el);
});
