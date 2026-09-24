import { ApiError } from "@/api/client";
import { formatBytes } from "./format";

/** One sentence for a toast: what went wrong and, when possible, what to do. */
export function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    const b = e.body;
    switch (b.error) {
      case "insufficient_disk":
        return `磁碟空間不足：需要 ${formatBytes(b.needed as number)}，剩餘 ${formatBytes(b.free as number)}`;
      case "local_path_forbidden":
        return "遠端連線不能使用本機路徑";
      case "input_not_found":
        return `找不到檔案或資料夾：${b.path}`;
      case "upload_not_found":
        return "上傳紀錄已不存在，請重新加入檔案";
      case "upload_incomplete":
        return "還有檔案沒有上傳完成";
      case "duplicate_input":
        return "同一個上傳檔案不能加入兩次";
      case "already_converting":
        return "這個檔案已經在轉換中";
      case "disk_full":
        return "伺服器磁碟已滿，上傳中斷；清出空間後按重試即可從中斷處續傳";
      case "upload_write_failed":
        return "伺服器無法寫入上傳的檔案";
      case "upload_too_large":
        return `檔案超過上傳上限 ${formatBytes(b.limit as number)}`;
      case "sha_mismatch":
        return "上傳內容的檢查碼不符，請重新上傳";
      case "unauthorized":
        return "需要 API token：請到 Settings 輸入";
      case "bad_host":
      case "cross_origin":
        return "伺服器拒絕了這個來源的請求";
      case "task_running":
        return "這個 task 還在執行中";
      case "source_missing":
        return `原始檔已不存在${b.path ? `：${b.path}` : ""}`;
      case "not_a_local_source":
        return "上傳的檔案沒有新版本可以重轉";
      case "setup_running":
        return "已有安裝在進行中";
      case "engine_busy":
        return `${b.engine === "unknown" ? "有工作" : b.engine} 正在轉換中，等它結束後再安裝`;
      case "output_missing":
        return "輸出目錄已不存在";
      case "tokenizer_unavailable":
        return "tokenizer 尚未下載，請先執行 aidoc setup";
      case "invalid_settings":
        return `設定值無效：${b.detail}`;
      case "token_readonly":
        return "token 只能在啟動 aidoc serve 時設定";
      case "network_error":
        return "連不上伺服器，請確認 aidoc serve 仍在執行";
      default:
        return `${b.error}${e.status ? `（HTTP ${e.status}）` : ""}`;
    }
  }
  if (e instanceof TypeError) return "連不上伺服器，請確認 aidoc serve 仍在執行";
  return e instanceof Error ? e.message : String(e);
}
