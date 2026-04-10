from typing import Literal

from algopy import arc4


Hash32 = arc4.StaticArray[arc4.Byte, Literal[32]]
