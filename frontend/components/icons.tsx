import type { SVGProps } from "react";

/** A small inline icon set: 24-unit grid, 1.75 stroke, inherits colour from text. */
type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Icon({ size = 18, children, ...rest }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      {...rest}
    >
      {children}
    </svg>
  );
}

export const IconCheck = (p: IconProps) => (
  <Icon {...p}><path d="M5 12.5l4.2 4.2L19 7" /></Icon>
);
export const IconCheckCircle = (p: IconProps) => (
  <Icon {...p}><circle cx="12" cy="12" r="9" /><path d="M8.2 12.3l2.6 2.6 5-5.4" /></Icon>
);
export const IconAlert = (p: IconProps) => (
  <Icon {...p}><path d="M12 3.8l9 15.6H3z" /><path d="M12 10v4.2M12 17.2v.01" /></Icon>
);
export const IconXCircle = (p: IconProps) => (
  <Icon {...p}><circle cx="12" cy="12" r="9" /><path d="M9 9l6 6M15 9l-6 6" /></Icon>
);
export const IconClock = (p: IconProps) => (
  <Icon {...p}><circle cx="12" cy="12" r="9" /><path d="M12 7.5V12l3 2" /></Icon>
);
export const IconDash = (p: IconProps) => (
  <Icon {...p}><circle cx="12" cy="12" r="9" /><path d="M8.5 12h7" /></Icon>
);
export const IconArrowRight = (p: IconProps) => (
  <Icon {...p}><path d="M5 12h14M13 6l6 6-6 6" /></Icon>
);
export const IconArrowLeft = (p: IconProps) => (
  <Icon {...p}><path d="M19 12H5M11 6l-6 6 6 6" /></Icon>
);
export const IconPlay = (p: IconProps) => (
  <Icon {...p}><path d="M8 5.5v13l10.5-6.5z" fill="currentColor" stroke="none" /></Icon>
);
export const IconPause = (p: IconProps) => (
  <Icon {...p}><path d="M8.5 5.5v13M15.5 5.5v13" strokeWidth={2.5} /></Icon>
);
export const IconRestart = (p: IconProps) => (
  <Icon {...p}><path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3" /><path d="M4 4.5v4h4" /></Icon>
);
export const IconPencil = (p: IconProps) => (
  <Icon {...p}><path d="M4 20h4L19 9l-4-4L4 16z" /><path d="M13.5 6.5l4 4" /></Icon>
);
export const IconSparkle = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 3.5l1.8 5.2 5.2 1.8-5.2 1.8L12 17.5l-1.8-5.2L5 10.5l5.2-1.8z" />
    <path d="M19 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z" />
  </Icon>
);
export const IconShield = (p: IconProps) => (
  <Icon {...p}><path d="M12 3l7.5 3v5.5c0 4.6-3.2 8.3-7.5 9.5-4.3-1.2-7.5-4.9-7.5-9.5V6z" /><path d="M8.6 12.2l2.4 2.4 4.5-4.8" /></Icon>
);
export const IconFile = (p: IconProps) => (
  <Icon {...p}><path d="M6.5 3h7.5l4.5 4.5V21h-12z" /><path d="M13.5 3v5h5M9 13h6M9 16.5h4" /></Icon>
);
export const IconSun = (p: IconProps) => (
  <Icon {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" /></Icon>
);
export const IconMoon = (p: IconProps) => (
  <Icon {...p}><path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z" /></Icon>
);
export const IconQuestion = (p: IconProps) => (
  <Icon {...p}><circle cx="12" cy="12" r="9" /><path d="M9.7 9.4a2.4 2.4 0 1 1 3.4 2.2c-.7.3-1.1.9-1.1 1.6v.4M12 16.8v.01" /></Icon>
);
export const IconFlag = (p: IconProps) => (
  <Icon {...p}><path d="M5.5 21V4M5.5 4.5h11l-2 4 2 4h-11" /></Icon>
);
export const IconLayers = (p: IconProps) => (
  <Icon {...p}><path d="M12 4l8.5 4.5L12 13 3.5 8.5z" /><path d="M3.5 12.5L12 17l8.5-4.5M3.5 16.5L12 21l8.5-4.5" /></Icon>
);
export const IconChart = (p: IconProps) => (
  <Icon {...p}><path d="M4 19.5h16" /><path d="M6.5 15l4-4.5 3 3L19 7" /><circle cx="19" cy="7" r="1.2" fill="currentColor" /></Icon>
);
