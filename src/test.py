# -*- coding: utf-8 -*-
from tasks.display import *
from celeryconfig import config
import unittest

class DisplayTests(unittest.TestCase):
    def test_save_display_data(self):
        title = "The page cannot be found"
        description = "����JFIFHH��C    $ '' & 0P40 , ,0bFJ : Ptfzxrfpn��������np���������|������������C '' $ $ 0*0^44^��p������������������������������������������������������� '' ��  ��� }  ! 1AQa '' q2�� # B��R�� $ 3br�  % & ' ( ) *456789 : CDEFGHIJSTUVWXYZcdefghijstuvwxyz���������������������������������������������������������������������������  ���w ! 1AQaq '' 2B���� # 3R�br�  $ 4� % � & ' ( ) *56789 : CDEFGHIJSTUVWXYZcdefghijstuvwxyz��������������������������������������������������������������������������  ? ���� ( �� ( �� ( �� ( �� ( ��*����cS����V��E�� # B� � P��9� ( � , I��4| } ���u�h��M��u0+�G� ( ��cu�T { v����Mo� ; ���qV�'�� �� �6��� �� �R���������s� ���� [ B�B��3��JI� > ��\��3�����n=������ F�Aj���sV�jfd�y��T��R�v�JS��� O�� ������yB���"
        url = "http://celebrate.ls.no/English/Animations/Science/overled_eng.swf"
        publisher = "eun.org"
        save_display_data(title, description, publisher, url, config)

if __name__ == "__main__":
    unittest.main()
