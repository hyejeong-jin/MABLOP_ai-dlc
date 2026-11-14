import type {
  ResearchSource,
  ResearchSummaryData,
  ResearchSummaryItem,
  SourceLabel as SourceLabelKind,
} from "../api";
import { SourceLabel } from "./SourceLabel";

/** Returns a normalized URL only for http/https; anything else is rejected. */
export function safeExternalUrl(raw: unknown): string | null {
  if (typeof raw !== "string" || !raw.trim()) return null;
  try {
    const url = new URL(raw.trim());
    return url.protocol === "http:" || url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}

function sourceName(source: ResearchSource): string {
  if (source.title) return source.title;
  if (source.domain) return source.domain;
  const url = safeExternalUrl(source.url ?? source.normalizedUrl);
  return url ? new URL(url).hostname : "출처";
}

function SourceLinks({ sources }: { sources: ResearchSource[] }) {
  return (
    <ul className="source-meta" aria-label="연결된 출처">
      {sources.map((source) => {
        const href = safeExternalUrl(source.url ?? source.normalizedUrl);
        const name = sourceName(source);
        return (
          <li key={source.sourceId}>
            {href ? (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {name}
                <span className="sr-only"> (새 창에서 열기)</span>
              </a>
            ) : (
              <span>{name}</span>
            )}
            {source.domain && source.title ? <span> · {source.domain}</span> : null}
          </li>
        );
      })}
    </ul>
  );
}

interface BlockProps {
  item: ResearchSummaryItem;
  kind: SourceLabelKind;
  sources: Map<string, ResearchSource>;
}

function SummaryBlock({ item, kind, sources }: BlockProps) {
  // A "fact" without any resolvable source must not be shown as a fact (Req 9.5).
  const linked =
    kind === "fact"
      ? (item.sourceIds ?? []).map((id) => sources.get(id)).filter((s): s is ResearchSource => !!s)
      : [];
  const effective: SourceLabelKind = kind === "fact" && linked.length === 0 ? "unverified" : kind;
  return (
    <li className="summary-block">
      <SourceLabel kind={effective} />
      <p className="summary-block-text">{item.text}</p>
      {effective === "fact" ? <SourceLinks sources={linked} /> : null}
    </li>
  );
}

interface ResearchSummaryProps {
  summary?: ResearchSummaryData | null;
  sources?: ResearchSource[];
}

function CitationLinks({ ids, order, sources }: { ids?: string[]; order: Map<string, number>; sources: Map<string, ResearchSource> }) {
  const nums = (ids ?? []).filter((id) => order.has(id));
  if (nums.length === 0) return null;
  return (
    <span className="citation-list">
      {nums.map((id) => {
        const n = order.get(id)!;
        const s = sources.get(id);
        const href = s ? safeExternalUrl(s.url ?? s.normalizedUrl) : null;
        return href ? (
          <a key={id} href={href} target="_blank" rel="noopener noreferrer" aria-label={`출처 ${n}`}>
            [{n}]
          </a>
        ) : (
          <span key={id}>[{n}]</span>
        );
      })}
    </span>
  );
}

export function ResearchSummary({ summary, sources = [] }: ResearchSummaryProps) {
  if (!summary) return null;
  const byId = new Map(sources.map((s) => [s.sourceId, s]));
  const order = new Map(sources.map((s, i) => [s.sourceId, i + 1]));
  const sections = summary.sections ?? [];
  const titleIdeas = summary.titleIdeas ?? [];
  const keywords = summary.keywords ?? [];
  const hasNew = sections.length > 0 || !!summary.overview;
  const groups: Array<[SourceLabelKind, ResearchSummaryItem[] | undefined]> = [
    ["fact", summary.facts],
    ["generated", summary.generatedInterpretation],
    ["user-input", summary.userInput],
    ["unverified", summary.unverified],
  ];
  const blocks = hasNew
    ? []
    : groups.flatMap(([kind, items]) =>
        (items ?? []).map((item, index) => ({ kind, item, key: `${kind}-${index}` })),
      );

  return (
    <section className="research-summary" aria-label="연구 요약">
      {summary.partial && (
        <p role="status" className="source-meta">
          부분 결과입니다{summary.omitted ? `: ${summary.omitted}` : "."}
        </p>
      )}
      {hasNew ? (
        <>
          {summary.overview ? <p className="research-overview">{summary.overview}</p> : null}
          {sections.map((section, si) => (
            <div key={`section-${si}`} className="research-section">
              <h4 className="research-section-title">{section.heading}</h4>
              <ul className="research-points">
                {(section.points ?? []).map((point, pi) => (
                  <li key={pi}>
                    {point.text} <CitationLinks ids={point.sourceIds} order={order} sources={byId} />
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {titleIdeas.length > 0 && (
            <div className="research-section">
              <h4 className="research-section-title">제목 아이디어</h4>
              <ul className="research-points">
                {titleIdeas.map((t, i) => (
                  <li key={i}>{t}</li>
                ))}
              </ul>
            </div>
          )}
          {keywords.length > 0 && (
            <ul className="chip-list" aria-label="키워드">
              {keywords.map((k, i) => (
                <li key={i} className="chip">{k}</li>
              ))}
            </ul>
          )}
        </>
      ) : blocks.length === 0 ? (
        <p className="source-meta">표시할 요약이 없습니다.</p>
      ) : (
        <ul className="summary-block-list">
          {blocks.map(({ kind, item, key }) => (
            <SummaryBlock key={key} item={item} kind={kind} sources={byId} />
          ))}
        </ul>
      )}
    </section>
  );
}