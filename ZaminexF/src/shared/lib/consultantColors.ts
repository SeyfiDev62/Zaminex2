// =============================================================================
//  Consultant marker colors (shared by every map in the app)
// =============================================================================
//  Maps that colour markers by consultant (property create/edit map, admin
//  dashboard distribution map) must give each consultant a distinct, stable
//  color that never repeats, and a brand-new consultant must automatically
//  pick up a fresh color. The palette is therefore assigned by stable index:
//  the unique consultant ids present in the data are sorted deterministically
//  and each one takes the next slot, so the same consultant always keeps the
//  same color and a new consultant lands on the first unused slot.
// =============================================================================

/** Sixteen high-contrast marker colors, ordered for maximum separation. */
export const CONSULTANT_MARKER_COLORS: string[] = [
  "#0BB68A", // emerald (primary)
  "#EF4444", // red
  "#3B82F6", // blue
  "#F59E0B", // amber
  "#8B5CF6", // violet
  "#EC4899", // pink
  "#14B8A6", // teal
  "#F97316", // orange
  "#6366F1", // indigo
  "#84CC16", // lime
  "#06B6D4", // cyan
  "#A855F7", // purple
  "#E11D48", // rose
  "#22C55E", // green
  "#EAB308", // yellow
  "#64748B", // slate
];

/** Neutral color for unassigned records (no consultant on file). */
export const CONSULTANT_FALLBACK_COLOR = "#94A3B8";

/**
 * The marker color of one consultant.
 *
 * `id` — the consultant to look up (string or number).
 * `ids` — every consultant id present in the current map data (used to build
 * the stable order). Passing the same list on every render keeps colors
 * deterministic; the function is pure and cheap, so per-marker calls are fine.
 */
export function consultantMarkerColor(
  id: string | number | null | undefined,
  ids: Array<string | number | null | undefined>
): string {
  if (id === null || id === undefined || id === "") return CONSULTANT_FALLBACK_COLOR;
  const key = String(id);

  const unique: string[] = [];
  const seen = new Set<string>();
  ids.forEach((x) => {
    if (x === null || x === undefined || x === "") return;
    const s = String(x);
    if (!seen.has(s)) {
      seen.add(s);
      unique.push(s);
    }
  });

  // Numeric first (property/consultant ids are integers), then any stray
  // string ids — stable across renders and across data refreshes.
  unique.sort((a, b) => {
    const na = Number(a);
    const nb = Number(b);
    if (Number.isFinite(na) && Number.isFinite(nb)) return na - nb;
    return a.localeCompare(b);
  });

  const index = unique.indexOf(key);
  if (index === -1) return CONSULTANT_FALLBACK_COLOR;
  return CONSULTANT_MARKER_COLORS[index % CONSULTANT_MARKER_COLORS.length];
}
