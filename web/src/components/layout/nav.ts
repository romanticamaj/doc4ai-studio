import { FileUp, Layers, Library, Plug, Scissors, SlidersHorizontal, type LucideIcon } from "lucide-react";

/** The MCP admin page. Not "/mcp": that path is the MCP endpoint itself (loading it in a browser gets its 401). */
export const MCP_PAGE = "/mcp-admin";

export type NavItem = { to: string; label: string; hint: string; icon: LucideIcon };

export const NAV: NavItem[] = [
  { to: "/convert", label: "Convert", hint: "上傳並轉換", icon: FileUp },
  { to: "/jobs", label: "Jobs", hint: "工作與進度", icon: Layers },
  { to: "/library", label: "Library", hint: "已轉換文件", icon: Library },
  { to: "/chunks", label: "Chunks", hint: "RAG 切段", icon: Scissors },
  { to: MCP_PAGE, label: "MCP", hint: "AI 連線與 token", icon: Plug },
  { to: "/settings", label: "Settings", hint: "引擎與設定", icon: SlidersHorizontal },
];
