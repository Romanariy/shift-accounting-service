import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const DJANGO_API_BASE_URL =
  process.env.DJANGO_API_BASE_URL ?? "http://127.0.0.1:8000";

type RouteContext = {
  params: Promise<{
    path?: string[];
  }>;
};

async function proxyRequest(request: NextRequest, context: RouteContext) {
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
      body: hasBody ? await request.text() : undefined,
    });
    const contentType = response.headers.get("Content-Type") ?? "";

    if (contentType.includes("spreadsheetml")) {
      return new Response(await response.arrayBuffer(), {
        status: response.status,
        headers: {
          "Content-Type": contentType,
          "Content-Disposition":
            response.headers.get("Content-Disposition") ?? "attachment; filename=report.xlsx",
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
  } catch {
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

