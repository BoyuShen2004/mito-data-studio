import type { DatasetMetadata } from "./types/project";

/** The free-text biomedical metadata fields, shown wherever a dataset is edited.
 *
 * Derived facts (shape, resolution, counts) and structured maps from
 * dataset.json (label_classes, channel_names) are deliberately absent — those
 * come from the files, not from typing.
 */
export const METADATA_FIELDS: { key: keyof DatasetMetadata; label: string }[] = [
  { key: "organism", label: "Organism / species" },
  { key: "tissue", label: "Tissue or organ" },
  { key: "cell_type", label: "Cell type" },
  { key: "imaging_modality", label: "Imaging modality" },
  { key: "imaging_instrument", label: "Imaging instrument / microscope" },
  { key: "experimental_condition", label: "Experimental condition" },
  { key: "sample_condition", label: "Sample condition" },
  { key: "dataset_source", label: "Dataset source" },
  { key: "publication", label: "Publication / reference" },
  { key: "notes", label: "Notes" },
];
