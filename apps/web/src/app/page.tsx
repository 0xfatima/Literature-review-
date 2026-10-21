import { redirect } from "next/navigation";
export default function Home() {
  redirect("/projects"); // middleware sends signed-out visitors on to /login
}
