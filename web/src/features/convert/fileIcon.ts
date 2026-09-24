import { File, FileAudio, FileCode, FileImage, FileSpreadsheet, FileText, Presentation, type LucideIcon } from "lucide-react";
import { extOf } from "@/lib/format";

const MAP: Record<string, LucideIcon> = {
  pdf: FileText, doc: FileText, docx: FileText, txt: FileText, md: FileText, rtf: FileText, epub: FileText,
  xls: FileSpreadsheet, xlsx: FileSpreadsheet, csv: FileSpreadsheet,
  ppt: Presentation, pptx: Presentation,
  png: FileImage, jpg: FileImage, jpeg: FileImage, tif: FileImage, tiff: FileImage, bmp: FileImage, webp: FileImage, gif: FileImage,
  html: FileCode, htm: FileCode, xml: FileCode, json: FileCode,
  mp3: FileAudio, wav: FileAudio, m4a: FileAudio,
};

export function fileIcon(name: string): LucideIcon {
  return MAP[extOf(name)] ?? File;
}
