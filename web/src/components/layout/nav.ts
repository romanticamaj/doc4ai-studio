import { FileUp, Layers, Library, Plug, Scissors, SlidersHorizontal, type LucideIcon } from "lucide-react";

export type NavItem = { to: string; label: string; hint: string; icon: LucideIcon };

export const NAV: NavItem[] = [
  { to: "/convert", label: "Convert", hint: "上傳並轉換", icon: FileUp },
  { to: "/jobs", label: "Jobs", hint: "工作與進度", icon: Layers },
  { to: "/library", label: "Library", hint: "已轉換文件", icon: Library },
  { to: "/chunks", label: "Chunks", hint: "RAG 切段", icon: Scissors },
  { to: "/mcp", label: "MCP", hint: "AI 連線與 token", icon: Plug },
  { to: "/settings", label: "Settings", hint: "引擎與設定", icon: SlidersHorizontal },
];
