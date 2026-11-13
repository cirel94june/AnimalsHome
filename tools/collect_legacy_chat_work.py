"""Collect explicitly named old AI webpages without changing any original message.

Run as the application account, with its normal working directory. Dry-run by
default; --apply calls exactly the same source-checked collector as the chat UI.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'aion-chat'))
from chat_artifacts import collect_saved_message


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind',choices=['conv','room'],required=True)
    parser.add_argument('--message-id',action='append',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    for message_id in args.message_id:
        result=await collect_saved_message(args.kind,message_id,dry_run=not args.apply)
        print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':asyncio.run(main())
