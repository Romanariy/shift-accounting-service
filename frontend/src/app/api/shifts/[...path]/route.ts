import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const DJANGO_API_BASE_URL =
  process.env.DJANGO_API_BASE_URL ?? "http://127.0.0.1:8000";

type RouteContext = {
  params: Promise<{
    path?: string[];
  }>;
};

// Bound even chunked multipart requests before forwarding their original bytes/boundary.
async function boundedBody(request: NextRequest) {
  const limit = 12 * 1024 * 1024;
  if (Number(request.headers.get("content-length") || 0) > limit) throw new RangeError("upload");
  const reader = request.body?.getReader();
  if (!reader) return undefined;
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > limit) { await reader.cancel(); throw new RangeError("upload"); }
    chunks.push(value);
  }
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.length; }
  return result;
}

async function proxyRequest(request: NextRequest, context: RouteContext) {
  const origin = request.headers.get("origin");
  if (!["GET", "HEAD"].includes(request.method) && origin && new URL(origin).host !== request.headers.get("host")) {
    return NextResponse.json({ error: "Запрос с другого сайта отклонён." }, { status: 403 });
  }
  const { path = [] } = await context.params;
  const apiPath = path.join("/");
  const search = request.nextUrl.search;
  const suffix = apiPath ? `${apiPath}${apiPath.endsWith(".xlsx") ? "" : "/"}` : "";
  const targetUrl = `${DJANGO_API_BASE_URL}/api/shifts/${suffix}${search}`;
  const hasBody = !["GET", "HEAD"].includes(request.method);

  try {
    const response = await fetch(targetUrl, {
      method: request.method,
      cache: "no-store",
      headers: {
        "Content-Type": request.headers.get("Content-Type") ?? "application/json",
      },
      body: hasBody ? await boundedBody(request) : undefined,
    });
    const contentType = response.headers.get("Content-Type") ?? "";

    if (contentType.includes("spreadsheetml") || contentType.startsWith("image/")) {
      return new Response(response.body, {
        status: response.status,
        headers: {
          "Content-Type": contentType,
          ...(contentType.includes("spreadsheetml") ? {"Content-Disposition": response.headers.get("Content-Disposition") ?? "attachment; filename=report.xlsx"} : {}),
          "X-Content-Type-Options": "nosniff",
          "Cache-Control": "private, no-store",
        },
      });
    }

    const text = await response.text();

    return new Response(text, {
      status: response.status,
      headers: {
        "Content-Type": contentType || "application/json; charset=utf-8",
        "Cache-Control": "no-store",
      },
    });
  } catch (error) {
    if (error instanceof RangeError) return NextResponse.json({error:"Загрузка превышает 12 МБ."}, {status:413});
    return NextResponse.json(
      { error: "Django backend is unavailable." },
      { status: 503 }
    );
  }
}

export function GET(request: NextRequest, context: RouteContext) {
  return proxyRequest(request, context);
}

export function POST(request: NextRequest, context: RouteContext) {
  return proxyRequest(request, context);
}

export function PUT(request: NextRequest, context: RouteContext) {
  return proxyRequest(request, context);
}

export function DELETE(request: NextRequest, context: RouteContext) {
  return proxyRequest(request, context);
}
