// NestJS controller: @Controller("admin") + @Get("stats") → GET /admin/stats.
import { Controller, Get } from "@nestjs/common";

@Controller("admin")
export class AdminController {
  @Get("stats")
  getStats() {
    return { count: 0 };
  }
}
