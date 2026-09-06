export type QualityScoreKind = "gold_standard" | "reviewer_agreement";

/**
 * Every metric is nullable on purpose. The backend records an absent
 * measurement rather than 0.0, so the UI must render `null` as "—" and never
 * as a zero — "not measured" and "measured badly" must not look alike.
 */
export interface QualityScore {
  id: number;
  kind: QualityScoreKind;
  reference_submission: number | null;
  dice: number | null;
  iou: number | null;
  precision: number | null;
  recall: number | null;
  instance_f1: number | null;
  false_merges: number | null;
  false_splits: number | null;
  variation_of_information: number | null;
  provider: string;
  detail: Record<string, unknown>;
  computed_at: string;
}

export interface QualityByKind {
  count: number;
  mean_dice: number | null;
  mean_instance_f1: number | null;
}

export interface QualityPerson {
  user_id: number;
  username: string;
  scored: number;
  mean_dice: number | null;
  mean_instance_f1: number | null;
  false_merges: number | null;
  false_splits: number | null;
}

export interface ProjectQuality {
  by_kind: Record<string, QualityByKind>;
  people: QualityPerson[];
  gold_standard_volumes: {
    id: number;
    name: string;
    reference_submission_id: number | null;
  }[];
}

export interface AnnotatorQuality {
  username: string;
  /** null when never measured. Render as "—". */
  score: number | null;
  history: {
    kind: QualityScoreKind;
    dice: number | null;
    instance_f1: number | null;
    computed_at: string;
  }[];
}
