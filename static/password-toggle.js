document.querySelectorAll("[data-password-toggle]").forEach((button) => {
  const input = document.getElementById(button.dataset.passwordToggle);

  if (!input) {
    return;
  }

  button.addEventListener("click", () => {
    const shouldShow = input.type === "password";
    input.type = shouldShow ? "text" : "password";
    button.textContent = shouldShow ? "Ocultar" : "Ver";
    button.setAttribute(
      "aria-label",
      shouldShow ? "Ocultar contraseña" : "Ver contraseña"
    );
  });
});
