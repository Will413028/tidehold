/** Backend shapes that more than one feature reads (features do not import each other). */

type Clause = {
  rule: Record<string, unknown>;
  /** cut from the stored wording by the rule's verified span, never the model's text */
  clause: string;
  warning_clause: string | null;
};

/** The real operator whose published wording a version quotes (the demo seed only). */
export type QuotedFrom = { name: string; url: string; accessed: string };

export type PolicyVersion = {
  id: string;
  created_at: string;
  compilation_id: string | null;
  wording: string;
  quoted_from: QuotedFrom | null;
  warning_source: string | null;
  /** the product headings the rules sit under; a trip binds one of them, or none */
  products: string[];
  rules: Clause[];
  rulings: Record<string, unknown>[];
};
