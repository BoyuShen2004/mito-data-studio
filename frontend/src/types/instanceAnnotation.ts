// Per-instance morphology and QA flags.
//
// The vocabulary is served by the backend (`/instance-annotations/`) rather
// than duplicated here, so adding a phenotype stays a backend-only change.
// These unions exist for type-safety at the call sites that name a specific
// value; anything rendering a list must iterate the served vocabulary.

export type MitoMorphology =
  | "normal"
  | "elongated"
  | "fragmented"
  | "swollen"
  | "donut"
  | "mega"
  | "cristae_loss"
  | "mitophagy";

export type InstanceQaFlag =
  | "uncertain"
  | "boundary_truncated"
  | "needs_split"
  | "needs_merge"
  | "false_positive";

export interface VocabularyEntry {
  value: string;
  label: string;
}

export interface InstanceVocabulary {
  morphology: VocabularyEntry[];
  qa_flags: VocabularyEntry[];
}

export interface InstanceAnnotation {
  label_id: number;
  /** Empty means "unclassified" — deliberately not the same as `normal`. */
  morphology: MitoMorphology | "";
  qa_flags: InstanceQaFlag[];
  note: string;
  review_worthy: boolean;
  updated_by: string | null;
  updated_at: string | null;
}

export interface InstanceAnnotationResponse {
  task?: number;
  volume: number;
  vocabulary: InstanceVocabulary;
  annotations: InstanceAnnotation[];
}

export interface InstanceSummary {
  total_annotated: number;
  morphology: Record<string, number>;
  /** Instances with a flag or note but no phenotype. Never a morphology value. */
  morphology_unclassified: number;
  qa_flags: Record<string, number>;
  vocabulary: InstanceVocabulary;
}

/** A partial update. An omitted field is left unchanged by the server. */
export interface InstanceAnnotationPatch {
  morphology?: MitoMorphology | "";
  qa_flags?: InstanceQaFlag[];
  note?: string;
}
