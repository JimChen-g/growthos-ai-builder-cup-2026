// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title GrowthOS Audit Anchor
/// @notice Stores privacy-preserving audit batch roots; raw ledger data stays off-chain.
contract AuditAnchor {
    struct Anchor {
        bytes32 batchIdHash;
        bytes32 previousRoot;
        uint64 recordCount;
        uint64 anchoredAt;
        uint8 mode;
        address submitter;
    }

    address public immutable owner;
    mapping(bytes32 root => Anchor) public anchors;

    error EmptyBatch();
    error InvalidRoot();
    error RootAlreadyAnchored();
    error Unauthorized();

    event AuditBatchAnchored(
        bytes32 indexed root,
        bytes32 indexed batchIdHash,
        uint64 recordCount,
        uint8 mode,
        bytes32 previousRoot,
        address indexed submitter
    );

    constructor() {
        owner = msg.sender;
    }

    function anchor(
        bytes32 root,
        bytes32 batchIdHash,
        uint64 recordCount,
        uint8 mode,
        bytes32 previousRoot
    ) external {
        if (msg.sender != owner) revert Unauthorized();
        if (root == bytes32(0)) revert InvalidRoot();
        if (recordCount == 0) revert EmptyBatch();
        if (anchors[root].anchoredAt != 0) revert RootAlreadyAnchored();

        anchors[root] = Anchor({
            batchIdHash: batchIdHash,
            previousRoot: previousRoot,
            recordCount: recordCount,
            anchoredAt: uint64(block.timestamp),
            mode: mode,
            submitter: msg.sender
        });

        emit AuditBatchAnchored(
            root,
            batchIdHash,
            recordCount,
            mode,
            previousRoot,
            msg.sender
        );
    }
}
