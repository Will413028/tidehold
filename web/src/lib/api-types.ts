/** Backend shapes that more than one feature reads (features do not import each other). */

type Clause = {
  rule: Record<string, unknown>;
  /** cut from the stored wording by the rule's verified span, never the model's text */
  clause: string;
  warning_clause: string | null;
};

export type PolicyVersion = {
  id: string;
  created_at: string;
  compilation_id: string | null;
  wording: string;
  warning_source: string | null;
  /** the product headings the rules sit under; a trip binds one of them, or none */
  products: string[];
  rules: Clause[];
  rulings: Record<string, unknown>[];
};
