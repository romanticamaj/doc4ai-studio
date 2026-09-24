/** A page with a folded corner and a page-marker tick: the product turns documents into page-anchored text. */
export function BrandMark({ className = "size-6" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <path d="M6 2.5h8.2L19 7.3V20a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 20V4A1.5 1.5 0 0 1 6.5 2.5Z" fill="var(--primary)" />
      <path d="M14 2.5V6a1.5 1.5 0 0 0 1.5 1.5H19" fill="none" stroke="var(--primary-foreground)" strokeOpacity=".55" strokeWidth="1.2" />
      <path d="M8.5 11.5h7M8.5 14.5h7M8.5 17.5h4" stroke="var(--primary-foreground)" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}
