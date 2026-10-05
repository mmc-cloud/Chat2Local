import { Badge } from "@/components/ui/badge";
export function StatusBadge({
  online,
  label,
}: {
  online: boolean;
  label: string;
}) {
  return (
    <Badge
      variant="outline"
      className={online ? "status-badge is-online" : "status-badge"}
    >
      <i className={online ? "status-dot online" : "status-dot"} />
      {label}
    </Badge>
  );
}
