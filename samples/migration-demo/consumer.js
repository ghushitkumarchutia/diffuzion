export async function loadUser(id, apiUrl) {
  await fetch("https://api.diffuzion.example/v1/users", {
    method: "POST",
    body: JSON.stringify({ name: "Ada", status: "paused" }),
  });
  await fetch(`https://api.diffuzion.example/v1/users/${id}`);
  await fetch("/legacy");
  await fetch(apiUrl);
}
