import type {
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

const CONTROL =
  "w-full rounded-md border border-border-strong bg-background px-3 py-2 text-sm text-foreground placeholder:text-[#5f7089] focus:border-accent focus:outline-none disabled:opacity-60";

export function Field({
  label,
  htmlFor,
  hint,
  error,
  required,
  children,
}: {
  label: string;
  htmlFor?: string;
  hint?: ReactNode;
  error?: string | null;
  required?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="flex items-baseline justify-between text-xs font-medium text-foreground">
        <span>
          {label}
          {required ? <span className="ml-0.5 text-[#fca5a5]">*</span> : null}
        </span>
        {hint ? <span className="text-[11px] font-normal text-muted">{hint}</span> : null}
      </label>
      {children}
      {error ? (
        <p className="text-[11px] text-[#fca5a5]" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function Input({ className = "", ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={`${CONTROL} ${className}`} {...props} />;
}

export function Textarea({
  className = "",
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={`${CONTROL} min-h-20 resize-y font-mono text-xs ${className}`} {...props} />;
}

export function Select({
  className = "",
  children,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={`${CONTROL} ${className}`} {...props}>
      {children}
    </select>
  );
}

export function FormError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div
      role="alert"
      className="rounded-md border border-[#5f2229] bg-[#2a1114] px-3 py-2 text-xs text-[#fca5a5]"
    >
      {message}
    </div>
  );
}
