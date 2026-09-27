export type ChartPoint = {
  label: string;
  series: string;
  value: number;
  unit: string | null;
  observation_id?: number;
};

export type ChartSpec = {
  type: "bar" | "line";
  title: string;
  x_axis: string;
  y_axis: string;
  data: ChartPoint[];
};

export type DatasetSource = {
  kind: "dataset";
  dataset_id?: number;
  bps_table_id?: string | null;
  title?: string | null;
};

export type DocumentSource = {
  kind: "document";
  document_id: string;
};

export type SourceChip = DatasetSource | DocumentSource;

export type QuestionResponse = {
  conversation_id: string;
  answer: string | null;
  chart: ChartSpec | null;
  sources: SourceChip[];
  source_route: string | null;
  chart_error: string | null;
  plan_error: string | null;
  sql_error: string | null;
  sql_warnings: string | null;
  retrieval_error: string | null;
};

export type ConversationSummary = {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
};

export type ConversationMessage = {
  id: number;
  role: string;
  content: string;
  chart: ChartSpec | null;
  sources: SourceChip[];
  source_route: string | null;
  chart_error: string | null;
  created_at: string;
};

export type ConversationDetail = ConversationSummary & {
  messages: ConversationMessage[];
};

export type ThreadMessage = {
  key: string;
  role: "user" | "assistant";
  content: string;
  chart: ChartSpec | null;
  sources: SourceChip[];
  chartError: string | null;
  planError: string | null;
  sqlError: string | null;
  sqlWarnings: string | null;
  retrievalError: string | null;
  pending: boolean;
};

export function asChart(value: ChartSpec | null | undefined): ChartSpec | null {
  if (!value || (value.type !== "bar" && value.type !== "line")) {
    return null;
  }
  if (!Array.isArray(value.data) || value.data.length === 0) {
    return null;
  }
  return value;
}

export function messageFromHistory(message: ConversationMessage): ThreadMessage | null {
  if (message.role !== "user" && message.role !== "assistant") {
    return null;
  }
  return {
    key: `saved-${message.id}`,
    role: message.role,
    content: message.content,
    chart: asChart(message.chart),
    sources: message.sources ?? [],
    chartError: message.chart_error,
    planError: null,
    sqlError: null,
    sqlWarnings: null,
    retrievalError: null,
    pending: false,
  };
}

export function messageFromResult(result: QuestionResponse): ThreadMessage {
  return {
    key: `result-${result.conversation_id}-${Date.now()}`,
    role: "assistant",
    content: result.answer ?? "",
    chart: asChart(result.chart),
    sources: result.sources ?? [],
    chartError: result.chart_error,
    planError: result.plan_error,
    sqlError: result.sql_error,
    sqlWarnings: result.sql_warnings,
    retrievalError: result.retrieval_error,
    pending: false,
  };
}
