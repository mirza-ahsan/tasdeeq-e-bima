export type RiskBand = "low" | "medium" | "high";

export interface Option { value: unknown; label: string }

export interface Question {
  feature: string;
  text: string;
  help_text: string;
  kind: "choice" | "number";
  options: Option[];
  unit: string | null;
  expected_info_gain: number;
}

export interface AnsweredField {
  feature: string;
  label: string;
  value: unknown;
  value_label: string;
}

export interface Step {
  session_id: string;
  done: boolean;
  probability: number;
  risk_band: RiskBand;
  n_answered: number;
  max_questions: number;
  question: Question | null;
  stop_reason: string | null;
  answered: AnsweredField[];
}

export interface FlaggedField {
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
  n_answered: number;
  stop_reason: string | null;
  flagged_fields: FlaggedField[];
  carc: Carc | null;
  carc_alternatives: Carc[];
  explanation: { text: string; source: "qwen" | "fallback"; model: string | null };
  answered: AnsweredField[];
}

export interface Health {
  status: string;
  models_loaded: boolean;
  n_training_claims: number;
  test_auc: number;
  qwen: Record<string, string>;
  active_sessions: number;
}
