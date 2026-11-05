import { StatusBadge } from "@/components/StatusBadge";

/** Marks an OAuth connection (a grant) among PATs: the neutral status pill, so it never reads as a state. */
export function OAuthPill({ className }: { className?: string }) {
  return (
    <StatusBadge status="oauth" className={className}>
      OAuth
    </StatusBadge>
  );
}
