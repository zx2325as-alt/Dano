from pathlib import Path

path = Path("back/dano/onboarding/page_onboard.py")
text = path.read_text(encoding="utf-8")
old = '''        # 发布硬闸门:verify_publishable(self_check 等证据)+ verify_reviewed(capture 仍按既定放行,审核闸门在上方编排层把守)
        pub = await T.publish_asset(run_id, {"asset_draft_id": d["asset_draft_id"],
                                             "validation_run_ids": rp["validation_run_ids"],
                                             "review_run_ids": review_run_ids})
'''
new = '''        # P4 最终权威封印：修复/审核阶段可操作草稿；发布前把最终 executable artifact 与
        # Transaction IR 双向 hash 绑定，然后对**封印后的新草稿**重跑 self_check/replay/review。
        # 这样任何发布后或证据生成后的 api_request/IR 变更都会使 authority 校验失败。
        from dano.execution.page.transaction_authority_p4 import authority_required, seal_api_request
        if authority_required(api_request):
            try:
                api_request = seal_api_request(api_request)
            except ValueError as exc:
                return {"ok": False, "stage": "authority", "status": IngestionStatus.REJECTED.value,
                        "action": action, "reason": str(exc)}
            body, params, req_fields, opt_fields = _build_page_body(api_request, action, title, required)
            d = await T.save_draft(run_id, {"system_instance_id": sid, "asset_type": "page_script",
                                            "asset_key": action, "body": body})
            rp = await T.sandbox_replay(run_id, {"asset_draft_id": d["asset_draft_id"],
                                                 "sample_inputs": sample_inputs or {},
                                                 "live": False})
            if not rp.get("passed"):
                return {"ok": False, "stage": "authority", "status": IngestionStatus.REJECTED.value,
                        "action": action, "reason": "Transaction IR 封印后的确定性复验未通过",
                        "detail": rp.get("structured_output")}
            if T._review_board is not None:
                final_review = await T.request_review(run_id, {"asset_draft_id": d["asset_draft_id"]})
                review_run_ids = final_review.get("review_run_ids", []) or []
                if not final_review.get("all_passed", False):
                    reasons = [f"{v.get('role')}: {r}" for v in (final_review.get("verdicts") or [])
                               if not v.get("passed") for r in (v.get("reasons") or ["未通过"])]
                    return {"ok": False, "stage": "authority_review",
                            "status": IngestionStatus.NEEDS_CLARIFICATION.value,
                            "action": action, "clarifications": reasons,
                            "reason": "封印后的最终资产未通过三模型复核"}

        # 发布硬闸门:验证与审核证据均绑定最终(封印后)草稿。
        pub = await T.publish_asset(run_id, {"asset_draft_id": d["asset_draft_id"],
                                             "validation_run_ids": rp["validation_run_ids"],
                                             "review_run_ids": review_run_ids})
'''
count = text.count(old)
if count != 1:
    raise SystemExit(f"expected one publish block, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
