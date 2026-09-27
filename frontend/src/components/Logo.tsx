type LogoProps = {
  size?: number;
};

export function Logo({ size = 36 }: LogoProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 32 32"
      width={size}
      height={size}
      fill="none"
      aria-hidden="true"
    >
      <rect width="32" height="32" rx="6" fill="#0d5c63" />
      <rect x="8" y="6" width="16" height="20" rx="1.5" fill="#f4f0e8" />
      <rect x="11" y="10" width="10.5" height="1.75" rx="0.5" fill="#5f8c90" />
      <rect x="11" y="14.25" width="5" height="2.5" rx="0.5" fill="#0d5c63" />
      <rect x="19" y="14.25" width="2.5" height="2.5" rx="0.4" fill="#0d5c63" />
      <rect x="11" y="19.25" width="10.5" height="1.75" rx="0.5" fill="#5f8c90" />
    </svg>
  );
}
