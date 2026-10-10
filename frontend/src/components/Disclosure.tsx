import { useEffect, useId, useState, type ReactNode } from "react";

/** Secondary information: defer its first mount, then retain drafts on collapse.
 * Entity owners must key this component when navigating to a different entity. */
export default function Disclosure({ title, count, summary, children, className = "", defaultOpen = false, collapsible = true }: {
  title: string;
  count?: number;
  summary?: ReactNode;
  children: ReactNode;
  className?: string;
  defaultOpen?: boolean;
  /** Plain sections for singleton collections; collapse only meaningful groups. */
  collapsible?: boolean;
}) {
  const id = useId();
  const [open, setOpen] = useState(defaultOpen || !collapsible);
  const [hasOpened, setHasOpened] = useState(defaultOpen || !collapsible);
  const expanded = !collapsible || open;
  useEffect(() => {
    if (!collapsible) { setOpen(true); setHasOpened(true); }
  }, [collapsible]);
  const label = <>
    <span className="disclosure-title">{title}</span>
    <span id={`${id}-summary`} className="disclosure-context">
      {count != null && <span className="section-tab-count" aria-label={`${count} items`}>{count}</span>}
      {summary && <span className="disclosure-summary">{summary}</span>}
    </span>
  </>;
  return <section className={`disclosure ${className}`}>
    <h3 className="disclosure-heading">
      {collapsible ? <button type="button" className="disclosure-toggle" aria-label={title}
        aria-describedby={count != null || summary ? `${id}-summary` : undefined}
        aria-expanded={open} aria-controls={id}
        onClick={() => {
          setHasOpened(true);
          setOpen(value => !value);
        }}>
        <span aria-hidden="true">{open ? "▾" : "▸"}</span>
        {label}
      </button> : <span className="disclosure-static-heading">{label}</span>}
    </h3>
    <div id={id} hidden={!expanded} className="disclosure-body">
      {(expanded || hasOpened) && children}
    </div>
  </section>;
}
