from dano.execution.page.transaction_ir import (
    SourceSpec,
    TransactionIR,
    ir_to_dict,
    validate_transaction_ir,
)


def test_root_list_records_path_survives_ir_serialization() -> None:
    ir = TransactionIR(
        sources=[SourceSpec(
            id="src_root",
            kind="http_list",
            url="/api/options",
            records_path=[],
        )]
    )

    serialized = ir_to_dict(ir)

    assert serialized["sources"][0]["records_path"] == []
    assert validate_transaction_ir(serialized) == []


def test_invalid_records_path_is_rejected() -> None:
    ir = {
        "version": "transaction-ir/v1",
        "sources": [{
            "id": "src_bad",
            "kind": "http_list",
            "url": "/api/options",
            "records_path": "rows",
        }],
    }

    assert validate_transaction_ir(ir) == [
        "sources[0].records_path must be a token path or [] for a root list"
    ]
