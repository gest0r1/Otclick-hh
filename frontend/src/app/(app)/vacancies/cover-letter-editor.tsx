"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import { Btn, Tag } from "@/components/otclick/ui";

type CoverLetterRow = {
  status: string;
  cover_letter_draft: string | null;
  cover_letter_meta: Record<string, unknown>;
};

type Props = {
  vacancyId: string;
  status: string;
  initialDraft: string | null;
  meta: Record<string, unknown>;
  onUpdated: (row: CoverLetterRow) => void;
};

export default function CoverLetterEditor({
  vacancyId,
  status,
  initialDraft,
  meta,
  onUpdated,
}: Props) {
  const [text, setText] = useState(initialDraft ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedText, setSavedText] = useState(initialDraft ?? "");

  useEffect(() => {
    setText(initialDraft ?? "");
    setSavedText(initialDraft ?? "");
  }, [initialDraft]);

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      const row = await apiFetch<CoverLetterRow>(`/api/vacancies/${vacancyId}/cover-letter/generate`, {
        method: "POST",
      });
      setText(row.cover_letter_draft ?? "");
      setSavedText(row.cover_letter_draft ?? "");
      onUpdated(row);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сформировать письмо");
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    const clean = text.trim();
    if (!clean) return;
    setBusy(true);
    setError(null);
    try {
      const row = await apiFetch<CoverLetterRow>(`/api/vacancies/${vacancyId}/cover-letter`, {
        method: "PUT",
        body: JSON.stringify({ text: clean }),
      });
      setText(row.cover_letter_draft ?? "");
      setSavedText(row.cover_letter_draft ?? "");
      onUpdated(row);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить письмо");
    } finally {
      setBusy(false);
    }
  }

  const factKeys = Array.isArray(meta.fact_keys)
    ? meta.fact_keys.filter((value): value is string => typeof value === "string")
    : [];
  const changed = text.trim() !== savedText.trim();
  const inTargetLength = text.length >= 500 && text.length <= 750;

  if (status === "selected") {
    return (
      <div style={{ marginTop: 14, paddingTop: 14, borderTop: "1px solid var(--line-2)" }}>
        <div style={{ fontSize: 12, fontWeight: 750, marginBottom: 8 }}>Сопроводительное письмо</div>
        <Btn kind="yellow" size="sm" loading={busy} onClick={generate}>
          сформировать черновик
        </Btn>
        {error && <div style={{ color: "var(--err)", fontSize: 12, marginTop: 8 }}>{error}</div>}
      </div>
    );
  }

  if (status !== "letter_draft" && status !== "approved") return null;

  return (
    <div style={{ marginTop: 14, paddingTop: 14, borderTop: "1px solid var(--line-2)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
        <div style={{ fontSize: 12, fontWeight: 750 }}>Сопроводительное письмо</div>
        <Tag tone={status === "approved" ? "ok" : "yellow"}>
          {status === "approved" ? "одобрено" : "черновик"}
        </Tag>
        <Tag tone={inTargetLength ? "ok" : "warn"}>{text.length} знаков</Tag>
      </div>

      <textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        readOnly={status === "approved"}
        aria-label="Черновик сопроводительного письма"
        style={{
          width: "100%",
          minHeight: 190,
          resize: "vertical",
          border: "1px solid var(--line)",
          borderRadius: 14,
          background: status === "approved" ? "var(--bg-deep)" : "var(--surface)",
          color: "var(--ink)",
          padding: 12,
          font: "inherit",
          fontSize: 13,
          lineHeight: 1.5,
          outline: "none",
        }}
      />

      {factKeys.length > 0 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
          <span style={{ fontSize: 11, color: "var(--muted)", alignSelf: "center" }}>Факты:</span>
          {factKeys.map((key) => <Tag key={key} tone="neutral">{key}</Tag>)}
        </div>
      )}

      {status === "letter_draft" && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
          <Btn kind="primary" size="sm" loading={busy} disabled={!changed || !text.trim()} onClick={save}>
            сохранить
          </Btn>
          <Btn kind="ghost" size="sm" disabled={busy || changed} onClick={generate}>
            перегенерировать
          </Btn>
        </div>
      )}
      {changed && (
        <div style={{ color: "var(--warn)", fontSize: 11, marginTop: 7 }}>
          Есть несохранённые изменения. Перегенерация доступна после сохранения или отмены правок.
        </div>
      )}
      {error && <div style={{ color: "var(--err)", fontSize: 12, marginTop: 8 }}>{error}</div>}
    </div>
  );
}
