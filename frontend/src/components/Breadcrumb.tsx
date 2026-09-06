import { Fragment } from "react";
import { Link } from "react-router-dom";

export interface Crumb {
  label: string;
  /** Omitted on the last segment: you are already there. */
  to?: string;
}

/**
 * `Cortex study / Tasks / cortex_01 z1–256 #42` — the same shape as GitHub's
 * `owner / repo / pull / 42`.
 *
 * Every segment is a link to a page that exists. `routes/backNavigation.ts`
 * still serves the ← Back control for deep links and reloads, but a page that
 * says where it is does not need Back to guess.
 */
export default function Breadcrumb({ items }: { items: Crumb[] }) {
  const visible = items.filter((item) => item.label);
  if (visible.length === 0) return null;
  return (
    <nav className="breadcrumb" aria-label="Breadcrumb">
      {visible.map((item, index) => (
        <Fragment key={`${item.label}-${index}`}>
          {index > 0 && <span className="breadcrumb-sep" aria-hidden="true">/</span>}
          {item.to && index < visible.length - 1 ? (
            <Link to={item.to}>{item.label}</Link>
          ) : (
            <span aria-current={index === visible.length - 1 ? "page" : undefined}>
              {item.label}
            </span>
          )}
        </Fragment>
      ))}
    </nav>
  );
}
