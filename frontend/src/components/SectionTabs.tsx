export interface SectionTab<T extends string> {
  id: T;
  label: string;
  count?: number;
}

export default function SectionTabs<T extends string>({
  tabs,
  active,
  onChange,
  label,
  sticky = false,
}: {
  tabs: SectionTab<T>[];
  active: T;
  onChange: (tab: T) => void;
  label: string;
  sticky?: boolean;
}) {
  return (
    <div
      className={`section-tabs${sticky ? " section-tabs-sticky" : ""}`}
      role="tablist"
      aria-label={label}
    >
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={active === tab.id}
          className={`section-tab${active === tab.id ? " section-tab-active" : ""}`}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
          {tab.count != null && <span className="section-tab-count">{tab.count}</span>}
        </button>
      ))}
    </div>
  );
}
