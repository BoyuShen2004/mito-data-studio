import type { DatasetMetadata } from "../types/project";

// Human-readable labels for the optional biomedical metadata fields. Rendered
// in this order; anything else present in the JSON is shown afterwards.
const LABELS: Record<string, string> = {
  organism: "Organism / species",
  tissue: "Tissue or organ",
  cell_type: "Cell type",
  imaging_modality: "Imaging modality",
  imaging_instrument: "Imaging instrument / microscope",
  experimental_condition: "Experimental condition",
  sample_condition: "Sample condition",
  dataset_source: "Dataset source",
  publication: "Publication / reference",
  description: "Description",
  notes: "Notes",
  split: "Split",
  label_classes: "Label classes",
  channel_names: "Channels",
  licence: "Licence",
};

/** Render a metadata value as text.
 *
 * Most values are strings, but nnU-Net's dataset.json contributes maps such as
 * `label_classes` ({background: 0, mitochondria: 1}), which must not end up as
 * "[object Object]".
 */
function formatValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(formatValue).join(", ");
  if (value && typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([k, v]) => `${k} (${formatValue(v)})`)
      .join(", ");
  }
  return String(value);
}

/** Whether a value has anything worth showing. */
function isEmpty(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "object") return Object.keys(value as object).length === 0;
  return String(value).trim() === "";
}

export default function MetadataCard({
  metadata,
  title = "Metadata",
  hideWhenEmpty = false,
}: {
  metadata?: DatasetMetadata | null;
  title?: string;
  /** Skip rendering entirely when there is nothing to show (avoids empty cards). */
  hideWhenEmpty?: boolean;
}) {
  const entries = Object.entries(metadata ?? {}).filter(([, v]) => !isEmpty(v));

  if (hideWhenEmpty && entries.length === 0) return null;

  const ordered = [
    ...Object.keys(LABELS).filter((k) => k in (metadata ?? {})),
    ...entries.map(([k]) => k).filter((k) => !(k in LABELS)),
  ];
  const seen = new Set<string>();

  return (
    <div className="card">
      <h3>{title}</h3>
      {entries.length === 0 ? (
        <p className="muted">No metadata recorded.</p>
      ) : (
        <table>
          <tbody>
            {ordered.map((key) => {
              if (seen.has(key)) return null;
              seen.add(key);
              const value = (metadata ?? {})[key];
              if (isEmpty(value)) return null;
              return (
                <tr key={key}>
                  <th>{LABELS[key] ?? key.replace(/_/g, " ")}</th>
                  <td>{formatValue(value)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
