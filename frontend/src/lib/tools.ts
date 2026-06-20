import i18n from '@/i18n';

/**
 * i18n key mapping for tool names.
 * Each entry maps a tool name to its translation key under the "tools." namespace.
 */
const TOOL_I18N_KEYS: Record<string, string> = {
  load_skill: "tools.load_skill",
  write_file: "tools.write_file",
  edit_file: "tools.edit_file",
  read_file: "tools.read_file",
  run_backtest: "tools.run_backtest",
  bash: "tools.bash",
  read_url: "tools.read_url",
  read_document: "tools.read_document",
  trading_connections: "tools.trading_connections",
  trading_select_connection: "tools.trading_select_connection",
  trading_check: "tools.trading_check",
  trading_account: "tools.trading_account",
  trading_positions: "tools.trading_positions",
  trading_orders: "tools.trading_orders",
  trading_quote: "tools.trading_quote",
  trading_history: "tools.trading_history",
  compact: "tools.compact",
  create_task: "tools.create_task",
  update_task: "tools.update_task",
  spawn_subagent: "tools.spawn_subagent",
};

/**
 * Returns a map of all known tool names to their translated labels.
 */
export function getToolLabels(): Record<string, string> {
  const labels: Record<string, string> = {};
  for (const [tool, key] of Object.entries(TOOL_I18N_KEYS)) {
    labels[tool] = i18n.t(key);
  }
  return labels;
}

/**
 * Backward-compatible alias. Prefer `getToolLabels()` or `localizeToolName()`.
 */
export const TOOL_LABELS: Record<string, string> = /* @deprecated */ getToolLabels();

export function localizeToolName(tool: string, fallback?: string): string {
  if (tool in TOOL_I18N_KEYS) {
    return i18n.t(TOOL_I18N_KEYS[tool]);
  }
  if (fallback !== undefined) {
    return fallback;
  }
  return tool;
}
