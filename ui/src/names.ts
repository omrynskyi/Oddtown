const TITLES = new Set(["brother", "sister", "father", "mother", "dr", "dr.", "mr", "mr.", "mrs", "mrs.", "ms", "ms.", "captain", "uncle", "aunt", "professor"]);

/** A short name for bubbles and the tree: the first name, or the title and name, like "Brother Anselm". */
export function shortName(fullName: string): string {
  const words = fullName.trim().split(/\s+/);
  return TITLES.has(words[0]?.toLowerCase()) && words.length > 1 ? `${words[0]} ${words[1]}` : words[0] ?? fullName;
}
