export type View = "city" | "tree" | "results";

const LABELS: Record<View, string> = { city: "City", tree: "Tree", results: "Results" };

/** The floating view switch, centered at the top of the screen. Results joins once the run ends. */
export function TabSwitcher({
  view,
  views,
  onChange,
  onHome,
}: {
  view: View;
  views: View[];
  onChange: (view: View) => void;
  onHome: () => void;
}) {
  return (
    <nav className="tabs" aria-label="Run navigation">
      <button type="button" className="home-tab" onClick={onHome}>
        Home
      </button>
      {views.map((v) => (
        <button key={v} type="button" className="tab" aria-pressed={view === v} onClick={() => onChange(v)}>
          {LABELS[v]}
        </button>
      ))}
    </nav>
  );
}
