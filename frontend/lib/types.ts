/** Mirrors backend/schemas.py. */

export type RiskBand = "low" | "medium" | "high";
export type CheckStatus = "ok" | "warn" | "issue" | "pending";

export interface Option {
  value: unknown;
  label: string;
}

export interface Question {
  feature: string;
  text: string;
  help_text: string;
  kind: "choice" | "number";
  options: Option[];
  unit: string | null;
  min_value: number | null;
  max_value: number | null;
  expected_info_gain: number;
}

export interface Field {
  feature: string;
  label: string;
  value: unknown;
  value_label: string;
  contribution_points: number;
  source: "bill" | "answer";
}

export interface Patient {
  age: number;
  gender: "M" | "F";
  label: string;
}

export interface Check {
  id: string;
  label: string;
  status: CheckStatus;
  detail: string;
}

export interface Bands {
  low_max: number;
  high_min: number;
}

export interface Change {
  feature: string;
  label: string;
  value_label: string;
  delta_points: number;
}

export interface Step {
  session_id: string;
  done: boolean;
  probability: number;
  risk_band: RiskBand;
  bands: Bands;
  base_rate: number;
  typical_probability: number;
  n_answered: number;
  min_questions: number;
  max_questions: number;
  question: Question | null;
  stop_reason: string | null;
  patient: Patient;
  claim: Field[];
  answered: Field[];
  checks: Check[];
  last_change: Change | null;
}

export interface Driver {
  feature: string;
  label: string;
  value_label: string;
  effect: "increases_risk" | "reduces_risk";
  contribution_points: number;
}

export interface Carc {
  code: string;
  confidence: number;
  official_description: string;
  plain_english: string;
  staff_action: string;
}

export interface Result {
  session_id: string;
  probability: number;
  probability_percent: number;
  risk_band: RiskBand;
  bands: Bands;
  base_rate: number;
  typical_probability: number;
  n_answered: number;
  stop_reason: string | null;
  drivers: Driver[];
  other_points: number;
  carc: Carc | null;
  carc_alternatives: Carc[];
  explanation: { text: string; source: "qwen" | "fallback"; model: string | null };
  patient: Patient;
  claim: Field[];
  answered: Field[];
  checks: Check[];
}

export interface ProcedureOption {
  value: string;
  label: string;
  typical_amount_pkr: number;
  needs_pre_auth: boolean;
}

export interface BandSummary {
  claims: number;
  share: number;
  rejection_rate: number | null;
}

export interface Meta {
  procedures: ProcedureOption[];
  pre_auth_amount_threshold_pkr: number;
  amount_limits: [number, number];
  age_limits: [number, number];
  min_questions: number;
  max_questions: number;
  bands: Bands;
  base_rate: number;
  n_training_claims: number;
  test_auc: number;
  verdicts: {
    claims: number;
    bands: Record<RiskBand, BandSummary>;
    rejections_flagged: number;
    mean_questions_approved: number;
    mean_questions_rejected: number;
    distinct_question_orders: number;
  };
}

export interface Health {
  status: "ok" | "degraded";
  models_loaded: boolean;
  qwen_configured: boolean;
  version: string;
}

export interface Intake {
  patient_age: number;
  patient_gender: "M" | "F";
  procedure_category: string;
  claim_amount_pkr: number;
}

export type DemoKind = "risky" | "clean";
