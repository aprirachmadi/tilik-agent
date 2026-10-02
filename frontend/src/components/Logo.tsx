type LogoProps = {
  size?: number;
};

export function Logo({ size = 36 }: LogoProps) {
  return (
    <img
      src="/tilik-logo.svg"
      width={size}
      height={size}
      alt=""
      aria-hidden="true"
      className="block shrink-0"
    />
  );
}
