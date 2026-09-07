"use client";

import { useState } from "react";
import { apiFetch } from "@/lib/api";
import { Btn, Card, Tag } from "@/components/otclick/ui";

type RunResult = {
  discovery: {
    sources: number;
    fetched: number;
    persisted: number;
    errors: number;
  };
  scoring: {
    found: number;
    scored: number;
    hard_filtered: number;
    archived: number;
    errors: number;
    skipped: number;
  };
};

export default function ManualRunPage() {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<RunResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function runNow() {
    setBusy(true);
    setError(null);
    try {
      const next = await apiFetch<RunResult>("/api/search-sources/run-now", { method: "POST" });
      setResult(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось выполнить поиск");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <Card>
        <div style={{ fontSize: 17, fontWeight: 700 }}>Ручной цикл поиска</div>
        <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 4, maxWidth: 760 }}>
          Выполняет discovery по всем включённым Search Sources и затем scoring новых вакансий. Это тот же путь, что у worker, только без ожидания 5 минут. Отправка HH здесь отсутствует.
        </div>
        <div style={{ marginTop: 14 }}>
          <Btn kind="yellow" size="sm" loading={busy} disabled={busy} onClick={runNow}>
            запустить поиск сейчас
          </Btn>
        </div>
      </Card>

      {result && (
        <Card>
          <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 10 }}>Результат последнего запуска</div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <Tag tone="neutral">источников {result.discovery.sources}</Tag>
            <Tag tone="neutral">получено {result.discovery.fetched}</Tag>
            <Tag tone="ok">новых/обновлено {result.discovery.persisted}</Tag>
            <Tag tone={result.discovery.errors ? "err" : "neutral"}>discovery errors {result.discovery.errors}</Tag>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
            <Tag tone="neutral">к оценке {result.scoring.found}</Tag>
            <Tag tone="ok">оценено {result.scoring.scored}</Tag>
            <Tag tone="warn">hard filter {result.scoring.hard_filtered}</Tag>
            <Tag tone="neutral">архив {result.scoring.archived}</Tag>
            <Tag tone={result.scoring.errors ? "err" : "neutral"}>score errors {result.scoring.errors}</Tag>
            <Tag tone="neutral">skipped {result.scoring.skipped}</Tag>
          </div>
        </Card>
      )}

      {error && (
        <Card>
          <div style={{ color: "var(--err)", fontSize: 13 }}>{error}</div>
        </Card>
      )}
    </div>
  );
}
