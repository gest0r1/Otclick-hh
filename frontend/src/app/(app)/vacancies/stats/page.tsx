"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import { Btn, Card, EmptyState, Tag } from "@/components/otclick/ui";

type SourceStats = {
  runs?: number;
  fetched?: number;
  persisted?: number;
  skipped_applied?: number;
  errors?: number;
};

type LastRun = {
  checked_at?: string;
  fetched?: number;
  persisted?: number;
  skipped_applied?: number;
  overlap_found?: boolean;
  error?: string | null;
};

type SearchSource = {
  id: string;
  name: string;
  source_type: string;
  enabled: boolean;
  last_error: string | null;
  cursor: {
    stats?: SourceStats;
    last_run?: LastRun;
  };
};

export default function SourceStatsPage() {
  const [rows, setRows] = useState<SearchSource[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await apiFetch<SearchSource[]>("/api/search-sources");
      setRows(data);
      setError(null);
    } catch (err) {
      setRows([]);
      setError(err instanceof Error ? err.message : "Не удалось загрузить статистику источников");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <Card>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 17, fontWeight: 700 }}>Статистика источников</div>
            <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 4 }}>
              Cumulative discovery stats сохраняются в cursor источника и не сбрасываются между запусками.
            </div>
          </div>
          <Btn kind="ghost" size="sm" onClick={load}>обновить</Btn>
        </div>
      </Card>

      {error && <div style={{ color: "var(--err)", fontSize: 12 }}>{error}</div>}

      {rows === null ? (
        <Card><div style={{ fontSize: 13, color: "var(--muted)" }}>Загрузка…</div></Card>
      ) : rows.length === 0 ? (
        <Card>
          <EmptyState title="Нет источников" description="Сначала добавьте Search URL в разделе Источники." />
        </Card>
      ) : (
        rows.map((row) => {
          const stats = row.cursor?.stats ?? {};
          const last = row.cursor?.last_run ?? {};
          const runs = stats.runs ?? 0;
          const fetched = stats.fetched ?? 0;
          const persisted = stats.persisted ?? 0;
          const yieldPct = fetched > 0 ? Math.round((persisted / fetched) * 100) : 0;
          return (
            <Card key={row.id}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <strong style={{ fontSize: 14 }}>{row.name}</strong>
                <Tag tone={row.enabled ? "ok" : "neutral"}>{row.enabled ? "включён" : "выключен"}</Tag>
                <Tag tone="neutral">{row.source_type}</Tag>
                {(stats.errors ?? 0) > 0 && <Tag tone="err">errors {stats.errors}</Tag>}
              </div>

              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
                <Tag tone="neutral">runs {runs}</Tag>
                <Tag tone="neutral">fetched {fetched}</Tag>
                <Tag tone="ok">persisted {persisted}</Tag>
                <Tag tone="neutral">yield {yieldPct}%</Tag>
                <Tag tone="neutral">already applied {stats.skipped_applied ?? 0}</Tag>
              </div>

              {runs > 0 && (
                <div style={{ marginTop: 10, fontSize: 12, color: "var(--muted)" }}>
                  Последний run: fetched {last.fetched ?? 0}, persisted {last.persisted ?? 0}, skipped {last.skipped_applied ?? 0}
                  {last.overlap_found === false ? " · overlap не найден" : ""}
                </div>
              )}
              {(last.error || row.last_error) && (
                <div style={{ marginTop: 7, fontSize: 12, color: "var(--err)", wordBreak: "break-word" }}>
                  {last.error || row.last_error}
                </div>
              )}
            </Card>
          );
        })
      )}
    </div>
  );
}
