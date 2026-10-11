export type View = "city" | "tree";

const TABS: { view: View; label: string }[] = [
  { view: "city", label: "City" },
  { view: "tree", label: "Tree" },
];

/** The floating City and Tree switch, centered at the top of the screen. */
export function TabSwitcher({ view, onChange, onHome }: { view: View; onChange: (view: View) => void; onHome: () => void }) {
  return (
    <nav className="tabs" aria-label="Run navigation">
      <button type="button" className="home-tab" onClick={onHome}>
        Home
      </button>
      {TABS.map((tab) => (
        <button
          key={tab.view}
          type="button"
          className="tab"
          aria-pressed={view === tab.view}
          onClick={() => onChange(tab.view)}
        >
          {tab.label}
        </button>
      ))}
    </nav>
  );
}
