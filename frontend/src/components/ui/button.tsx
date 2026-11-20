import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-accent-strong text-white hover:bg-[#2f74e0] border border-accent-strong disabled:bg-[#24344c] disabled:border-[#24344c] disabled:text-muted",
  secondary:
    "bg-surface-raised text-foreground hover:bg-[#1a2536] border border-border-strong disabled:text-muted",
  ghost: "bg-transparent text-muted hover:text-foreground hover:bg-surface-raised border border-transparent",
  danger:
    "bg-[#3a1418] text-[#fca5a5] hover:bg-[#4a1a1f] border border-[#5f2229] disabled:text-[#7f5257]",
};

const SIZES: Record<Size, string> = {
  sm: "h-8 px-3 text-xs gap-1.5",
  md: "h-9 px-4 text-sm gap-2",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

export function Button({
  variant = "secondary",
  size = "md",
  className = "",
  type = "button",
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      className={`inline-flex items-center justify-center rounded-md font-medium transition-colors disabled:cursor-not-allowed ${VARIANTS[variant]} ${SIZES[size]} ${className}`}
      {...props}
    />
  );
}
