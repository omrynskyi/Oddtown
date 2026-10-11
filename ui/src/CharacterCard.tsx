import { TRAIT_AXES, type Persona, type Traits } from "./events";
import { leaning, TRAIT_ENDS } from "./traits";

/** Who a character is, and where it sits on each of the six trait axes. */
export function CharacterCard({ persona }: { persona: Persona }) {
  return (
    <article className="card">
      <h2 className="card-name">{persona.name}</h2>
      <p className="card-line">{persona.one_liner}</p>
      <TraitBars traits={persona.traits} />
    </article>
  );
}

/** One bar per trait axis, growing from the middle toward the end the character leans to. */
export function TraitBars({ traits }: { traits: Traits }) {
  return (
    <dl className="card-traits">
      {TRAIT_AXES.map((axis) => {
        const value = traits[axis];
        const ends = TRAIT_ENDS[axis];
        const width = Math.abs(value) * 50;
        const left = value < 0 ? 50 - width : 50;
        return (
          <div className="trait" key={axis}>
            <dt className="visually-hidden">{ends.name}</dt>
            <dd className="trait-row" aria-label={`${ends.name}: ${leaning(axis, value)} (${value.toFixed(1)})`}>
              <span className="trait-end" aria-hidden="true">
                {ends.low}
              </span>
              <span className="trait-bar" aria-hidden="true">
                <span className="trait-fill" style={{ left: `${left}%`, width: `${width}%` }} />
              </span>
              <span className="trait-end trait-end-high" aria-hidden="true">
                {ends.high}
              </span>
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
